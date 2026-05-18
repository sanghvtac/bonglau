"""
Helper module để upload file lên Cloudflare R2.
Hỗ trợ upload song song nhiều file để tăng tốc.
"""
import os
import boto3
from botocore.config import Config
from concurrent.futures import ThreadPoolExecutor, as_completed

def get_r2_client():
    """Tạo S3 client trỏ tới Cloudflare R2."""
    return boto3.client(
        's3',
        endpoint_url=os.environ['R2_ENDPOINT'],
        aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],
        aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'],
        config=Config(
            signature_version='s3v4',
            max_pool_connections=50,  # tăng pool để hỗ trợ parallel upload
        ),
        region_name='auto',
    )

# Tạo client 1 lần dùng chung cho cả module (tránh tạo lại nhiều lần)
_client = None
def _get_client():
    global _client
    if _client is None:
        _client = get_r2_client()
    return _client

def _guess_content_type(remote_key):
    """Đoán MIME type theo đuôi file."""
    if remote_key.endswith('.json'):
        return 'application/json; charset=utf-8'
    elif remote_key.endswith('.m3u') or remote_key.endswith('.m3u8'):
        return 'application/vnd.apple.mpegurl'
    elif remote_key.endswith('.txt'):
        return 'text/plain; charset=utf-8'
    elif remote_key.endswith('.png'):
        return 'image/png'
    elif remote_key.endswith('.jpg') or remote_key.endswith('.jpeg'):
        return 'image/jpeg'
    elif remote_key.endswith('.webp'):
        return 'image/webp'
    else:
        return 'application/octet-stream'

def upload_file(local_path, remote_key, content_type=None):
    """Upload 1 file lên R2."""
    client = _get_client()
    bucket = os.environ['R2_BUCKET']

    if content_type is None:
        content_type = _guess_content_type(remote_key)

    extra_args = {
        'ContentType': content_type,
        'CacheControl': 'public, max-age=60',
    }

    try:
        client.upload_file(local_path, bucket, remote_key, ExtraArgs=extra_args)
        return (remote_key, True, None)
    except Exception as e:
        return (remote_key, False, str(e))

def upload_many(file_map, max_workers=10):
    """
    Upload nhiều file song song.
    file_map: dict {local_path: remote_key}
    max_workers: số luồng chạy song song (mặc định 10)
    """
    tasks = [(local, remote) for local, remote in file_map.items() if os.path.exists(local)]
    skipped = [local for local in file_map if not os.path.exists(local)]
    for s in skipped:
        print(f"[R2] Skip (not found): {s}")

    if not tasks:
        return

    success = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(upload_file, local, remote): remote for local, remote in tasks}
        for future in as_completed(futures):
            remote_key, ok, err = future.result()
            if ok:
                success += 1
            else:
                failed += 1
                print(f"[R2] FAILED: {remote_key} - {err}")

    print(f"[R2] Uploaded {success} file(s), {failed} failed")

def upload_folder(local_dir, remote_prefix='', max_workers=10):
    """
    Upload toàn bộ file trong 1 thư mục lên R2 (song song).
    - local_dir: thư mục local (vd 'thumbs')
    - remote_prefix: prefix trên R2 (vd 'thumbs') -> file sẽ là 'thumbs/xxx.png'
    """
    if not os.path.isdir(local_dir):
        print(f"[R2] Folder not found: {local_dir}")
        return

    file_map = {}
    for fname in os.listdir(local_dir):
        local_path = os.path.join(local_dir, fname)
        if os.path.isfile(local_path):
            remote_key = f"{remote_prefix}/{fname}" if remote_prefix else fname
            file_map[local_path] = remote_key

    upload_many(file_map, max_workers=max_workers)
