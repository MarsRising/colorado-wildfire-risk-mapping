import json
import boto3
import os
import urllib3
from datetime import datetime, timedelta

s3 = boto3.client('s3')
sagemaker = boto3.client('sagemaker')
secrets = boto3.client('secretsmanager')
http = urllib3.PoolManager()

BUCKET = 'wildfire-risk-colorado-mmm'
ROLE_ARN = 'arn:aws:iam::026024574150:role/wildfire-sagemaker-role'

def get_copernicus_token():
    """Get access token from Copernicus"""
    secret = secrets.get_secret_value(
        SecretId='wildfire/copernicus-credentials'
    )
    creds = json.loads(secret['SecretString'])
    
    from urllib.parse import urlencode
    encoded_data = urlencode({
        'grant_type': 'client_credentials',
        'client_id': creds['client_id'],
        'client_secret': creds['client_secret']
    }).encode('utf-8')
    
    response = http.request(
        'POST',
        'https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token',
        body=encoded_data,
        headers={
            'Content-Type': 'application/x-www-form-urlencoded'
        }
    )
    
    if response.status == 200:
        return json.loads(response.data)['access_token']
    else:
        raise Exception(
            f"Failed to get token: {response.data}"
        )

def search_sentinel2(token, tile_name, days_back=15):
    """Search for latest Sentinel-2 scene for a tile"""
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (
        datetime.now() - timedelta(days=days_back)
    ).strftime('%Y-%m-%d')
    
    tile_bounds = {
        'T13TDF': [-106.20, 40.56, -104.88, 41.55],
        'T13TDE': [-106.18, 39.66, -104.89, 40.65]
    }
    
    bounds = tile_bounds[tile_name]
    tile_code = tile_name[1:]
    
    search_body = json.dumps({
        'collections': ['SENTINEL-2'],
        'bbox': bounds,
        'datetime': f'{start_date}T00:00:00Z/{end_date}T23:59:59Z',
        'limit': 10,
        'filter': f'eo:cloud_cover < 20 AND s2:mgrs_tile = "{tile_code}"'
    }).encode('utf-8')
    
    response = http.request(
        'POST',
        'https://catalogue.dataspace.copernicus.eu/stac/v1/search',
        body=search_body,
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
    )
    
    if response.status == 200:
        features = json.loads(
            response.data
        ).get('features', [])
        if features:
            return features[0]
    
    return None

def download_band(token, scene, band, local_path):
    """Download a specific band from a Sentinel-2 scene"""
    assets = scene.get('assets', {})
    
    band_key = None
    for key in assets:
        if band in key and '10m' in key.lower():
            band_key = key
            break
    
    if not band_key:
        print(f"Band {band} not found in assets")
        return False
    
    download_url = assets[band_key].get('href', '')
    
    if not download_url:
        return False
    
    response = http.request(
        'GET',
        download_url,
        headers={'Authorization': f'Bearer {token}'},
        preload_content=False
    )
    
    if response.status == 200:
        with open(local_path, 'wb') as f:
            for chunk in response.stream(8192):
                f.write(chunk)
        response.release_conn()
        return True
    
    return False

def lambda_handler(event, context):
    print(f"Pipeline triggered: {datetime.now().isoformat()}")
    
    tiles = ['T13TDF', 'T13TDE']
    jobs_started = []
    
    try:
        # Get Copernicus token
        print("Getting Copernicus token...")
        token = get_copernicus_token()
        print("Token obtained!")
        
        for tile_name in tiles:
            print(f"\nProcessing {tile_name}...")
            
            # Default fallback S3 keys
            if tile_name == 'T13TDF':
                b04_key = 'raw/sentinel2/post-fire/T13TDF_20201122T175651_B04_10m.jp2'
                b08_key = 'raw/sentinel2/post-fire/T13TDF_20201122T175651_B08_10m.jp2'
            else:
                b04_key = 'raw/sentinel2/post-fire/T13TDE_20201122T175651_B04_10m.jp2'
                b08_key = 'raw/sentinel2/post-fire/T13TDE_20201122T175651_B08_10m.jp2'
            
            # Search for latest scene
            print(f"Searching for latest {tile_name} scene...")
            scene = search_sentinel2(token, tile_name, days_back=15)
            
            if scene:
                scene_date = scene['properties'][
                    'datetime'
                ][:10].replace('-', '')
                print(f"Found scene: {scene_date}")
                
                b04_path = f'/tmp/{tile_name}_B04.jp2'
                b08_path = f'/tmp/{tile_name}_B08.jp2'
                
                print(f"Downloading B04...")
                b04_ok = download_band(
                    token, scene, 'B04', b04_path
                )
                
                print(f"Downloading B08...")
                b08_ok = download_band(
                    token, scene, 'B08', b08_path
                )
                
                if b04_ok and b08_ok:
                    # Upload fresh imagery to S3
                    b04_key = f'raw/sentinel2/current/{tile_name}_{scene_date}_B04_10m.jp2'
                    b08_key = f'raw/sentinel2/current/{tile_name}_{scene_date}_B08_10m.jp2'
                    
                    s3.upload_file(
                        b04_path, BUCKET, b04_key
                    )
                    s3.upload_file(
                        b08_path, BUCKET, b08_key
                    )
                    print(f"Uploaded fresh imagery!")
                else:
                    print(f"Download failed — using fallback S3 imagery")
            else:
                print(f"No recent scene found — using fallback S3 imagery")
            
            # Start SageMaker Processing Job
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            job_name = f'wildfire-{tile_name.lower()}-{timestamp}'
            
            try:
                sagemaker.create_processing_job(
                    ProcessingJobName=job_name,
                    ProcessingResources={
                        'ClusterConfig': {
                            'InstanceCount': 1,
                            'InstanceType': 'ml.t3.medium',
                            'VolumeSizeInGB': 10
                        }
                    },
                    AppSpecification={
                        'ImageUri': '246618743249.dkr.ecr.us-west-2.amazonaws.com/sagemaker-scikit-learn:1.2-1-cpu-py3',
                        'ContainerEntrypoint': [
                            'python3',
                            '/opt/ml/processing/code/process.py'
                        ]
                    },
                    ProcessingInputs=[
                        {
                            'InputName': 'b04',
                            'S3Input': {
                                'S3Uri': f's3://{BUCKET}/{b04_key}',
                                'LocalPath': '/opt/ml/processing/input/b04',
                                'S3DataType': 'S3Prefix',
                                'S3InputMode': 'File'
                            }
                        },
                        {
                            'InputName': 'b08',
                            'S3Input': {
                                'S3Uri': f's3://{BUCKET}/{b08_key}',
                                'LocalPath': '/opt/ml/processing/input/b08',
                                'S3DataType': 'S3Prefix',
                                'S3InputMode': 'File'
                            }
                        },
                        {
                            'InputName': 'code',
                            'S3Input': {
                                'S3Uri': f's3://{BUCKET}/code/process.py',
                                'LocalPath': '/opt/ml/processing/code',
                                'S3DataType': 'S3Prefix',
                                'S3InputMode': 'File'
                            }
                        }
                    ],
                    ProcessingOutputConfig={
                        'Outputs': [
                            {
                                'OutputName': 'ndvi',
                                'S3Output': {
                                    'S3Uri': f's3://{BUCKET}/processed/tiles/{tile_name}/',
                                    'LocalPath': '/opt/ml/processing/output',
                                    'S3UploadMode': 'EndOfJob'
                                }
                            }
                        ]
                    },
                    RoleArn=ROLE_ARN
                )
                
                jobs_started.append(job_name)
                print(f"Started job: {job_name}")
                
            except Exception as e:
                print(f"Error starting job for {tile_name}: {e}")
    
    except Exception as e:
        print(f"Pipeline error: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': 'Pipeline triggered successfully',
            'jobs_started': jobs_started,
            'timestamp': datetime.now().isoformat()
        })
    }