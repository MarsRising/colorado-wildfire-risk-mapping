import json
import boto3
from boto3.dynamodb.conditions import Key
from decimal import Decimal

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('wildfire-risk-scores')

class DecimalEncoder(json.JSONEncoder):
    """Handle DynamoDB Decimal types"""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)

def lambda_handler(event, context):
    
    # CORS headers for web map access
    headers = {
        'Content-Type': 'application/json'
    }
    
    try:
        # Get query parameters
        params = event.get('queryStringParameters') or {}
        risk_level = params.get('risk_level')
        limit = int(params.get('limit', 300))
        
        # Scan DynamoDB for risk scores
        if risk_level:
            # Filter by risk level
            response = table.scan(
                FilterExpression='risk_level = :level',
                ExpressionAttributeValues={
                    ':level': risk_level.upper()
                },
                Limit=limit
            )
        else:
            # Return all scores
            response = table.scan()
        
        items = response.get('Items', [])
        
        # Format response for map
        risk_data = []
        for item in items:
            risk_data.append({
                'grid_cell_id': item.get('grid_cell_id'),
                'risk_score': float(item.get('risk_score', 0)),
                'risk_level': item.get('risk_level'),
                'timestamp': item.get('timestamp'),
                'ndvi_current': float(item.get('ndvi_current', 0)),
                'burn_probability': float(
                    item.get('burn_probability', 0)
                ),
                'model_version': item.get('model_version', 'v2')
            })
        
        # Sort by risk score descending
        risk_data.sort(
            key=lambda x: x['risk_score'], 
            reverse=True
        )
        
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps({
                'status': 'success',
                'count': len(risk_data),
                'risk_scores': risk_data
            }, cls=DecimalEncoder)
        }
        
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({
                'status': 'error',
                'message': str(e)
            })
        }