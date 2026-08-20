import json
import boto3
from decimal import Decimal

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('wildfire-risk-scores')

class DecimalEncoder(json.JSONEncoder):
    """Handle DynamoDB Decimal types"""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)

def get_all_items(risk_level=None):
    items = []
    scan_kwargs = {}
    if risk_level:
        scan_kwargs['FilterExpression'] = 'risk_level = :level'
        scan_kwargs['ExpressionAttributeValues'] = {':level': risk_level.upper()}

    while True:
        response = table.scan(**scan_kwargs)
        items.extend(response.get('Items', []))
        if 'LastEvaluatedKey' not in response:
            break
        scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
    return items

def lambda_handler(event, context):
    headers = {'Content-Type': 'application/json'}
    try:
        params = event.get('queryStringParameters') or {}
        risk_level = params.get('risk_level')

        items = get_all_items(risk_level)

        # Keep only the most recent reading per grid cell
        latest_by_cell = {}
        for item in items:
            cell_id = item.get('grid_cell_id')
            ts = item.get('timestamp', '')
            if cell_id not in latest_by_cell or ts > latest_by_cell[cell_id]['timestamp']:
                latest_by_cell[cell_id] = item

        risk_data = []
        for item in latest_by_cell.values():
            risk_data.append({
                'grid_cell_id': item.get('grid_cell_id'),
                'risk_score': float(item.get('risk_score', 0)),
                'risk_level': item.get('risk_level'),
                'timestamp': item.get('timestamp'),
                'ndvi_current': float(item.get('ndvi_current', 0)),
                'burn_probability': float(item.get('burn_probability', 0)),
                'drought_index': float(item.get('drought_index', 0)),
                'slope_degrees': float(item.get('slope_degrees', 0)),
                'days_since_fire': item.get('days_since_fire'),
                'model_version': item.get('model_version', 'v2')
            })

        risk_data.sort(key=lambda x: x['risk_score'], reverse=True)

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
            'body': json.dumps({'status': 'error', 'message': str(e)})
        }
