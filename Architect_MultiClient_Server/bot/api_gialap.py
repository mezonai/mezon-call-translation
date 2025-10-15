
from flask import Flask, request, jsonify
import jwt
import datetime
import os

app = Flask(__name__)

# Đọc public key
def load_public_key():
    try:
        with open('public-key.pem', 'r') as f:
            return f.read()
    except FileNotFoundError:
        print(" Không tìm thấy file public-key.pem")
        return None

PUBLIC_KEY = load_public_key()

@app.route('/api/enable-transcript', methods=['POST'])
def enable_transcript():


    auth_header = request.headers.get('Authorization')
    if not auth_header:
        return jsonify({
            'error': 'Missing Authorization header'
        }), 401
    
    try:
        token = auth_header.split(' ')[1]
    except IndexError:
        return jsonify({
            'error': 'Invalid Authorization header format. Use: Bearer <token>'
        }), 401
    
    try:
        if not PUBLIC_KEY:
            return jsonify({
                'error': 'Public key not loaded'
            }), 500
            

        decoded_payload = jwt.decode(
            token, 
            PUBLIC_KEY, 
            algorithms=['RS256']
        )
        # đoạn này nếu chú dùng js thì verify bằng jwt.verify(...), còn python thì decode đã bao gồm verify rồi

        meeting_code = decoded_payload.get('meetingCode')
        channel_id = decoded_payload.get('channelId')
        user_id = decoded_payload.get('userId')
        
        # đoạn này a giả lập data trả về, chú handle dưới này rồi trả về.
        
        response_data = {
            'status': 'success',
            'message': f'Transcript enabled for meeting {meeting_code}',
            'data': {
                'meetingCode': meeting_code,
                'channelId': channel_id,
                'userId': user_id,
                'action': 'enable_transcript'
            }
        }
        
        return jsonify(response_data), 200
        
    except jwt.ExpiredSignatureError:
        return jsonify({
            'error': 'Token has expired'
        }), 401
        
    except jwt.InvalidTokenError as e:
        return jsonify({
            'error': f'Invalid token: {str(e)}'
        }), 401
        
    except Exception as e:
        return jsonify({
            'error': f'Server error: {str(e)}'
        }), 500


if __name__ == '__main__':
    
    app.run(debug=True, host='0.0.0.0', port=5000)