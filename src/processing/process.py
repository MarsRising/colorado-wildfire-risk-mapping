
import subprocess
import sys

print("Installing dependencies...")
subprocess.check_call([
    sys.executable, '-m', 'pip',
    'install',
    'numpy==1.24.1',
    'rasterio==1.3.9',
    '--force-reinstall',
    '--quiet'
])

import numpy as np
import rasterio
from rasterio.windows import Window
import os
import json
import boto3
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

s3 = boto3.client('s3', region_name='us-west-2')
dynamodb = boto3.resource('dynamodb', region_name='us-west-2')
BUCKET = 'wildfire-risk-colorado-mmm'
TABLE_NAME = 'wildfire-risk-scores'
table = dynamodb.Table(TABLE_NAME)
tile_size = 256

# ── Live Drought Index ─────────────────────────────────────────────────
def get_drought_index():
    """Fetch current drought index for Colorado from NOAA"""
    try:
        url = (
            'https://usdmdataservices.unl.edu/api/'
            'StateStatistics/GetDroughtSeverityStatisticsByArea?'
            'aoi=co&startdate='
            + (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
            + '&enddate='
            + datetime.now().strftime('%Y-%m-%d')
            + '&statisticsType=1'
        )
        
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
            
            if data and len(data) > 0:
                latest = data[-1]
                
                d0 = float(latest.get('D0', 0))
                d1 = float(latest.get('D1', 0))
                d2 = float(latest.get('D2', 0))
                d3 = float(latest.get('D3', 0))
                d4 = float(latest.get('D4', 0))
                
                drought_idx = -(
                    (d0 * 0.5) +
                    (d1 * 1.0) +
                    (d2 * 2.0) +
                    (d3 * 3.0) +
                    (d4 * 4.0)
                ) / 100.0
                
                print(f"Live drought index: {drought_idx:.2f}")
                print(f"D0:{d0}% D1:{d1}% D2:{d2}% D3:{d3}% D4:{d4}%")
                return drought_idx
                
    except Exception as e:
        print(f"Could not fetch drought data: {e}")
    
    print("Using fallback drought index: -1.5")
    return -1.5

# ── Live Fire History ──────────────────────────────────────────────────
def get_fire_history():
    """
    Fetch recent fire detections from NASA FIRMS
    Returns dict of {(lat_rounded, lon_rounded): most_recent_fire_date}
    """
    fire_dates = {}
    
    try:
        url = (
            'https://firms.modaps.eosdis.nasa.gov/usfs/api/area/csv/'
            'VIIRS_SNPP_NRT/'
            '-106.5,39.5,-104.5,41.6/'
            '7'
        )
        
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as response:
            data = response.read().decode()
            lines = data.strip().split('\n')
            
            if len(lines) > 1:
                headers = lines[0].split(',')
                
                try:
                    lat_idx = headers.index('latitude')
                    lon_idx = headers.index('longitude')
                    date_idx = headers.index('acq_date')
                except ValueError:
                    print("Could not parse FIRMS headers")
                    return {}
                
                for line in lines[1:]:
                    if not line.strip():
                        continue
                    
                    cols = line.split(',')
                    if len(cols) <= max(lat_idx, lon_idx, date_idx):
                        continue
                    
                    try:
                        lat = round(float(cols[lat_idx]), 1)
                        lon = round(float(cols[lon_idx]), 1)
                        date_str = cols[date_idx]
                        
                        fire_date = datetime.strptime(
                            date_str, '%Y-%m-%d'
                        )
                        
                        key = (lat, lon)
                        if key not in fire_dates or fire_date > fire_dates[key]:
                            fire_dates[key] = fire_date
                            
                    except (ValueError, IndexError):
                        continue
                
                print(f"Found {len(fire_dates)} recent fire locations")
                
    except Exception as e:
        print(f"Could not fetch FIRMS data: {e}")
    
    return fire_dates

def get_days_since_fire(lat, lon, fire_history, table, tile_name, row_start, col_start):
    """
    Get days since last fire for a specific location
    1. Check NASA FIRMS for recent fire activity
    2. Fall back to last recorded value in DynamoDB + 5 days
    3. Last resort: Cameron Peak date
    """
    lat_r = round(lat, 1)
    lon_r = round(lon, 1)
    
    # Check 1 — NASA FIRMS recent detections
    min_days = None
    for dlat in [-0.2, -0.1, 0.0, 0.1, 0.2]:
        for dlon in [-0.2, -0.1, 0.0, 0.1, 0.2]:
            key = (
                round(lat_r + dlat, 1),
                round(lon_r + dlon, 1)
            )
            if key in fire_history:
                days = (datetime.now() - fire_history[key]).days
                if min_days is None or days < min_days:
                    min_days = days
    
    if min_days is not None:
        print(f"Active fire at ({lat:.2f}, {lon:.2f}): {min_days} days ago")
        return min_days
    
    # Check 2 — Last recorded value in DynamoDB
    try:
        grid_cell_id = f"{tile_name}_{row_start}_{col_start}"
        response = table.get_item(
            Key={
                'grid_cell_id': grid_cell_id,
                'timestamp': get_latest_timestamp(table, grid_cell_id)
            }
        )
        if 'Item' in response:
            last_days = int(
                response['Item'].get('days_since_fire', 0)
            )
            if last_days > 0:
                # Add 5 days since last update
                updated_days = last_days + 5
                return updated_days
    except Exception as e:
        print(f"Could not fetch DynamoDB history: {e}")
    
    # Check 3 — Cameron Peak as absolute last resort
    cameron_peak_date = datetime(2020, 12, 2)
    return (datetime.now() - cameron_peak_date).days

def get_latest_timestamp(table, grid_cell_id):
    """Get the most recent timestamp for a grid cell"""
    try:
        response = table.query(
            KeyConditionExpression='grid_cell_id = :id',
            ExpressionAttributeValues={':id': grid_cell_id},
            ScanIndexForward=False,
            Limit=1
        )
        if response['Items']:
            return response['Items'][0]['timestamp']
    except Exception as e:
        print(f"Could not query timestamp: {e}")
    return None

# ── Risk Score Function ────────────────────────────────────────────────
def compute_risk_score(
    ndvi_current, ndvi_change, burn_probability,
    drought_index, slope_degrees, days_since_fire,
    is_burn_scar
):
    if ndvi_current < 0.1:
        vegetation_score = 25
    elif ndvi_current < 0.3:
        vegetation_score = 20
    elif ndvi_current < 0.5:
        vegetation_score = 10
    else:
        vegetation_score = 5

    if ndvi_change < -0.3:
        ndvi_trend_score = 20
    elif ndvi_change < -0.2:
        ndvi_trend_score = 15
    elif ndvi_change < -0.1:
        ndvi_trend_score = 10
    elif ndvi_change < 0:
        ndvi_trend_score = 5
    else:
        ndvi_trend_score = 0

    burn_score = int(burn_probability * 20)

    if drought_index < -4:
        drought_score = 20
    elif drought_index < -3:
        drought_score = 15
    elif drought_index < -2:
        drought_score = 10
    elif drought_index < -1:
        drought_score = 5
    else:
        drought_score = 0

    if slope_degrees > 30:
        slope_score = 10
    elif slope_degrees > 20:
        slope_score = 7
    elif slope_degrees > 10:
        slope_score = 4
    else:
        slope_score = 1

    if days_since_fire > 3650:
        fire_history_score = 5
    elif days_since_fire > 1825:
        fire_history_score = 3
    elif days_since_fire > 365:
        fire_history_score = 1
    else:
        fire_history_score = 0

    total = min(100, (
        vegetation_score + ndvi_trend_score +
        burn_score + drought_score +
        slope_score + fire_history_score
    ))

    if total >= 75:
        risk_level = "CRITICAL"
    elif total >= 50:
        risk_level = "HIGH"
    elif total >= 25:
        risk_level = "MODERATE"
    else:
        risk_level = "LOW"

    return total, risk_level

# ── Main Processing ────────────────────────────────────────────────────
print("Fetching live environmental data...")

# Get live drought index
drought_index = get_drought_index()
print(f"Drought index: {drought_index:.2f}")

# Get recent fire history
print("Fetching NASA FIRMS fire history...")
fire_history = get_fire_history()
print(f"Fire locations found: {len(fire_history)}")

# Tile configurations
tiles = [
    {
        "name": "T13TDF",
        "post_b04": "raw/sentinel2/post-fire/T13TDF_20201122T175651_B04_10m.jp2",
        "post_b08": "raw/sentinel2/post-fire/T13TDF_20201122T175651_B08_10m.jp2",
        "pre_b04": "raw/sentinel2/pre-fire/T13TDF_20190711T174911_B04_10m.jp2",
        "pre_b08": "raw/sentinel2/pre-fire/T13TDF_20190711T174911_B08_10m.jp2",
        "top_lat": 41.5456,
        "left_lon": -106.1994,
        "bottom_lat": 40.5627,
        "right_lon": -104.8847,
    },
    {
        "name": "T13TDE",
        "post_b04": "raw/sentinel2/post-fire/T13TDE_20201122T175651_B04_10m.jp2",
        "post_b08": "raw/sentinel2/post-fire/T13TDE_20201122T175651_B08_10m.jp2",
        "pre_b04": "raw/sentinel2/pre-fire/T13TDE_20190711T174911_B04_10m.jp2",
        "pre_b08": "raw/sentinel2/pre-fire/T13TDE_20190711T174911_B08_10m.jp2",
        "top_lat": 40.6448,
        "left_lon": -106.1832,
        "bottom_lat": 39.6616,
        "right_lon": -104.8862,
    }
]

total_processed = 0
model = None

for tile_config in tiles:
    tile_name = tile_config["name"]
    print(f"\n{'='*40}")
    print(f"Processing {tile_name}...")
    print(f"{'='*40}")

    # Check for fresh imagery
    fresh_b04 = None
    fresh_b08 = None

    try:
        response = s3.list_objects_v2(
            Bucket=BUCKET,
            Prefix=f"raw/sentinel2/current/{tile_name}_"
        )
        if "Contents" in response:
            keys = [obj["Key"] for obj in response["Contents"]]
            b04_keys = [k for k in keys if "B04" in k]
            b08_keys = [k for k in keys if "B08" in k]

            if b04_keys and b08_keys:
                fresh_b04 = sorted(b04_keys)[-1]
                fresh_b08 = sorted(b08_keys)[-1]
                print(f"Using fresh imagery: {fresh_b04}")
    except Exception as e:
        print(f"Could not check fresh imagery: {e}")

    post_b04_key = fresh_b04 or tile_config["post_b04"]
    post_b08_key = fresh_b08 or tile_config["post_b08"]

    print("Downloading bands...")
    s3.download_file(BUCKET, post_b04_key, "/tmp/post_B04.jp2")
    s3.download_file(BUCKET, post_b08_key, "/tmp/post_B08.jp2")
    s3.download_file(BUCKET, tile_config["pre_b04"], "/tmp/pre_B04.jp2")
    s3.download_file(BUCKET, tile_config["pre_b08"], "/tmp/pre_B08.jp2")
    print("Bands downloaded!")

    img_height = 10980
    img_width = 10980
    top_lat = tile_config["top_lat"]
    left_lon = tile_config["left_lon"]
    bottom_lat = tile_config["bottom_lat"]
    right_lon = tile_config["right_lon"]

    # Load model once
    if model is None:
        print("Loading model...")
        subprocess.check_call([
            sys.executable, "-m", "pip",
            "install", "segmentation-models-pytorch",
            "--quiet"
        ])
        import torch
        import segmentation_models_pytorch as smp

        s3.download_file(
            BUCKET,
            "model/final/wildfire_unet_v2_best.pth",
            "/tmp/wildfire_unet_v2_best.pth"
        )
        model = smp.Unet(
            encoder_name="resnet34",
            encoder_weights=None,
            in_channels=3,
            classes=1,
        )
        model.load_state_dict(
            torch.load(
                "/tmp/wildfire_unet_v2_best.pth",
                map_location="cpu"
            )
        )
        model.eval()
        print("Model loaded!")
    else:
        import torch
        import segmentation_models_pytorch as smp

    sample_windows = [
        (row, col)
        for row in range(0, 10980, tile_size)
        for col in range(0, 10980, tile_size)
    ]

    processed = 0
    risk_scores = []

    with rasterio.open("/tmp/post_B04.jp2") as post_b04:
        with rasterio.open("/tmp/post_B08.jp2") as post_b08:
            with rasterio.open("/tmp/pre_B04.jp2") as pre_b04:
                with rasterio.open("/tmp/pre_B08.jp2") as pre_b08:

                    for row_start, col_start in sample_windows:
                        window = Window(
                            col_start, row_start,
                            tile_size, tile_size
                        )

                        post_red = post_b04.read(
                            1, window=window
                        ).astype(float)
                        post_nir = post_b08.read(
                            1, window=window
                        ).astype(float)
                        pre_red = pre_b04.read(
                            1, window=window
                        ).astype(float)
                        pre_nir = pre_b08.read(
                            1, window=window
                        ).astype(float)

                        if post_red.shape != (tile_size, tile_size):
                            continue

                        ndvi_post = (post_nir - post_red) / (
                            post_nir + post_red + 1e-10
                        )
                        ndvi_pre = (pre_nir - pre_red) / (
                            pre_nir + pre_red + 1e-10
                        )

                        ndvi_current = float(ndvi_post.mean())
                        ndvi_change = float(
                            ndvi_post.mean() - ndvi_pre.mean()
                        )

                        # Convert pixel to lat/lon
                        lat = top_lat - (
                            row_start / img_height
                        ) * (top_lat - bottom_lat)
                        lon = left_lon + (
                            col_start / img_width
                        ) * (right_lon - left_lon)

                        # Get live days since fire
                        days_since_fire = get_days_since_fire(
                            lat, lon, fire_history,
                            table, tile_name,
                            row_start, col_start
                        )

                        tile_data = np.stack(
                            [post_red, post_nir, ndvi_post],
                            axis=0
                        ).astype(np.float32)
                        tile_norm = (
                            tile_data - tile_data.mean()
                        ) / (tile_data.std() + 1e-10)

                        with torch.no_grad():
                            input_tensor = torch.tensor(
                                tile_norm
                            ).unsqueeze(0)
                            pred = torch.sigmoid(
                                model(input_tensor)
                            )
                            burn_prob = float(pred.mean())

                        risk, level = compute_risk_score(
                            ndvi_current=ndvi_current,
                            ndvi_change=ndvi_change,
                            burn_probability=burn_prob,
                            drought_index=drought_index,
                            slope_degrees=15.0,
                            days_since_fire=days_since_fire,
                            is_burn_scar=burn_prob > 0.5
                        )

                        record = {
                            "grid_cell_id": f"{tile_name}_{row_start}_{col_start}",
                            "timestamp": datetime.now().isoformat(),
                            "risk_score": risk,
                            "risk_level": level,
                            "ndvi_current": str(round(ndvi_current, 4)),
                            "ndvi_change": str(round(ndvi_change, 4)),
                            "burn_probability": str(round(burn_prob, 4)),
                            "drought_index": str(round(drought_index, 2)),
                            "days_since_fire": str(days_since_fire),
                            "latitude": str(round(lat, 4)),
                            "longitude": str(round(lon, 4)),
                            "model_version": "v2"
                        }

                        table.put_item(Item=record)
                        risk_scores.append(risk)
                        processed += 1

                        if processed % 200 == 0:
                            print(f"{tile_name}: {processed} tiles...")

    print(f"\n{tile_name} Complete!")
    print(f"  Tiles: {processed}")
    print(f"  Avg risk: {np.mean(risk_scores):.1f}")
    print(f"  CRITICAL: {sum(1 for r in risk_scores if r >= 75)}")
    print(f"  HIGH: {sum(1 for r in risk_scores if 50 <= r < 75)}")
    print(f"  MODERATE: {sum(1 for r in risk_scores if 25 <= r < 50)}")
    print(f"  LOW: {sum(1 for r in risk_scores if r < 25)}")

    total_processed += processed

print(f"\nALL TILES COMPLETE: {total_processed} total!")
