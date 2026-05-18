"""
Helper module để upload file lên Cloudflare R2.
Sử dụng boto3 (S3-compatible API).
"""
import os
import boto3
from botocore.config import Config

def get_r2_client():
    """Tạo S3 client trỏ tới Cloudflare R2."""
    return boto3.client(
        's3',
        endpoint_url=os.environ['R2_ENDPOINT'],
        aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],
        aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'],
        config=Config(signature_version='s3v4'),
        region_name='auto',
    )

def upload_file(local_path, remote_key, content_type=None):
    """
    Upload 1 file lên R2.
    - local_path: đường dẫn file trên máy/GitHub runner
    - remote_key: tên file trên R2 (vd: 'thiendinh_iptv.txt')
    - content_type: MIME type, tự đoán nếu None
    """
    client = get_r2_client()
    bucket = os.environ['R2_BUCKET']

    # Tự đoán content type theo đuôi file
    if content_type is None:
        if remote_key.endswith('.json'):
            content_type = 'application/json; charset=utf-8'
        elif remote_key.endswith('.m3u') or remote_key.endswith('.m3u8'):
            content_type = 'application/vnd.apple.mpegurl'
        elif remote_key.endswith('.txt'):
            content_type = 'text/plain; charset=utf-8'
        else:
            content_type = 'application/octet-stream'

    extra_args = {
        'ContentType': content_type,
        # Cache 60 giây để app TV refresh được nhanh khi có trận mới
        'CacheControl': 'public, max-age=60',
    }

    client.upload_file(local_path, bucket, remote_key, ExtraArgs=extra_args)
    print(f"[R2] Uploaded: {local_path} -> {remote_key}")

def upload_many(file_map):
    """
    Upload nhiều file 1 lúc.
    file_map: dict {local_path: remote_key}
    """
    for local, remote in file_map.items():
        if os.path.exists(local):
            upload_file(local, remote)
        else:
            print(f"[R2] Skip (not found): {local}")
