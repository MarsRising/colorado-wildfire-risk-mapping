# colorado-wildfire-risk-mapping
AI-powered wildfire risk mapping system using satellite imagery, computer vision, and AWS

![AWS](https://img.shields.io/badge/AWS-SageMaker%20%7C%20Lambda%20%7C%20DynamoDB-orange)
![Python](https://img.shields.io/badge/Python-3.10-blue)
![Model](https://img.shields.io/badge/Model-U--Net%20%7C%2095.3%25%20Accuracy-green)
![Status](https://img.shields.io/badge/Status-Live-brightgreen)

**Live Demo:** https://d380s90btm3jec.cloudfront.net

An end-to-end automated wildfire risk mapping system covering the 
Colorado Front Range — from the Wyoming border to south of Denver. 
Built entirely on AWS using satellite imagery, computer vision, 
and live environmental data.

---

## Live Map

The map scores wildfire risk across **3,500+ grid cells** covering 
northern Colorado including the Cameron Peak Fire zone, Estes Park, 
Boulder, Fort Collins, Denver and surrounding mountain areas.

Risk levels are color coded:
- 🔴 **Critical (75-100)** — Extreme danger
- 🟠 **High (50-74)** — Significant risk
- 🟡 **Moderate (25-49)** — Elevated risk  
- 🟢 **Low (0-24)** — Lower risk

---

## Architecture
Copernicus Satellite (every 5 days) -> AWS EventBridge (scheduled trigger) -> AWS Lambda (pipeline orchestration) ->SageMaker Processing Job (process.py) -> U-Net CV Model + Risk Scoring -> Amazon DynamoDB (risk scores) -> API Gateway REST API -> CloudFront + S3 (live map)
---

## AWS Services

| Service | Purpose |
|---|---|
| **SageMaker** | Model training, Ground Truth labeling, Processing Jobs |
| **Lambda** | Event-driven pipeline orchestration |
| **EventBridge** | Automated 5-day scheduling |
| **DynamoDB** | Risk score storage and retrieval |
| **API Gateway** | REST API serving risk scores |
| **CloudFront** | Global map delivery over HTTPS |
| **S3** | Satellite imagery, model artifacts, map hosting |
| **Secrets Manager** | Secure API credential storage |
| **IAM** | Least-privilege security controls |
| **CloudWatch** | Pipeline monitoring and logging |

---

## Machine Learning

### Computer Vision Model
- **Architecture:** U-Net with ResNet34 encoder (transfer learning)
- **Task:** Semantic segmentation — burn scar detection
- **Input:** 3-channel satellite imagery (Red, NIR, NDVI)
- **Training data:** Sentinel-2 satellite imagery + NASA FIRMS labels

### Model Versions
| Version | Training Data | Val Loss | Pixel Accuracy |

| V2 | 1,764 NASA FIRMS auto-labeled tiles | 0.2324 | **95.3%** |

### Model Metrics (V2)
- **Pixel Accuracy:** 95.3%
  
- **Precision:** 63.2%

- **Recall:** 35.1%

- **F1 Score:** 0.451

- **IoU Score:** 0.291

---

## Data Sources

| Source | Data | Cost |
|---|---|---|
| **Copernicus/ESA** | Sentinel-2 multispectral imagery (10m resolution) | Free |
| **NASA FIRMS** | VIIRS fire detection hotspots | Free |
| **NOAA** | Palmer Drought Severity Index | Free |
| **USGS** | Terrain/elevation data | Free |

---

## Risk Scoring Variables

The risk score (0-100) combines six signals:

1. **NDVI (Vegetation Health)** — dry stressed vegetation = more fuel
2. **NDVI Trend** — rapidly declining vegetation = drying out
3. **Burn Probability** — U-Net model confidence score
4. **Drought Index** — live NOAA Palmer Drought Severity Index
5. **Terrain Slope** — steeper slopes spread fire faster
6. **Days Since Last Fire** — fuel accumulation over time

---

## Automated Pipeline

The system runs fully automatically every 5 days:

1. **EventBridge** triggers Lambda at 8 AM Mountain Time
2. **Lambda** checks Copernicus API for fresh satellite imagery
3. Fresh imagery downloaded if available (< 20% cloud cover)
4. Falls back to latest S3 imagery if no fresh data found
5. **SageMaker Processing Job** runs with latest data
6. Live NOAA drought index fetched
7. NASA FIRMS checked for recent fire detections
8. Risk scores computed and saved to **DynamoDB**
9. Map automatically reflects updated scores

---

## Project Structure

colorado-wildfire-risk-mapping/
├── src/

│   ├── lambda/

│   │   ├── wildfire_preprocessor.py    # Pipeline orchestration

│   │   └── wildfire_api_handler.py     # API Gateway handler

│   ├── processing/

│   │   └── process.py                  # SageMaker processing script

│   └── notebooks/

│       ├── phase1_exploration.ipynb    # Data exploration

│       ├── phase3_v2.ipynb            # Model training

│       └── phase4_risk_scoring.ipynb  # Risk scoring pipeline

├── map/

│   └── index.html                      # Interactive map frontend

└── docs/

└── architecture.png                # System architecture diagram


## 💡 Key Technical Decisions

**Why U-Net?**
U-Net is the gold standard architecture for satellite image 
segmentation. Its encoder-decoder structure with skip connections 
preserves spatial detail critical for burn scar boundary detection.

**Why NASA FIRMS for training labels?**
Manual labeling of 31 tiles (V1) produced a val loss of 0.71. 
Switching to NASA FIRMS fire detection data for automatic label 
generation expanded training data 57x and improved val loss to 
0.23 — a 3x improvement with minimal manual effort.

**Why batch inference over real-time endpoints?**
SageMaker real-time endpoints cost ~$50-100/month running 24/7. 
Since imagery updates every 5 days, batch processing jobs cost 
pennies per run and are architecturally cleaner.

**Why EventBridge Scheduler over cron Lambda?**
EventBridge Scheduler provides timezone-aware scheduling, 
built-in retry logic, and dead-letter queue support — more 
robust than a simple cron expression.

---

## Author

**Margot** — [GitHub: MarsRising](https://github.com/MarsRising)

MS Data Analytics — Data Science  
AWS AI Practitioner | AWS ML Engineer Associate (in progress)

---
