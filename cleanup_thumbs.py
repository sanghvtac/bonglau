"""
Dọn dẹp ảnh thumbs cũ hơn N ngày trên cả 2 nơi:
1. GitHub: xóa file local (git auto-commit sẽ tự push commit delete)
2. Cloudflare R2: gọi API delete cho từng file
"""
import os
import subprocess
from datetime import datetime, timedelta, timezone
import boto3
from botocore.config import Config

# Cấu hình
THUMBS_DIR = "thumbs"
DAYS_TO_KEEP = 2  # Giữ lại ảnh trong 2 ngày gần nhất

# ──────────────────────────────────────────────
# Helper: Lấy thời điểm commit mới nhất chạm vào 1 file
# ──────────────────────────────────────────────
def get_file_commit_time(filepath):
    """
    Trả về datetime (UTC) của commit mới nhất chạm vào file này.
    Nếu file chưa được commit (mới tạo trong run hiện tại) → trả về now.
    """
    try:
        # %ct = unix timestamp của committer date
        result = subprocess.run(
            ['git', 'log', '-1', '--format=%ct', '--', filepath],
            capture_output=True, text=True, check=True
        )
        timestamp = result.stdout.strip()
        if not timestamp:
            # File chưa có trong git history (mới tạo trong run này)
            return datetime.now(timezone.utc)
        return datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
    except Exception as e:
        print(f"[WARN] Không lấy được commit time cho {filepath}: {e}")
        return datetime.now(timezone.utc)

# ──────────────────────────────────────────────
# 1. Dọn ảnh cũ trên GitHub (xóa file local)
# ──────────────────────────────────────────────
def cleanup_github_thumbs(days=DAYS_TO_KEEP):
    if not os.path.isdir(THUMBS_DIR):
        print(f"[GitHub] Thư mục {THUMBS_DIR} không tồn tại, bỏ qua")
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    deleted = 0
    kept = 0

    for fname in os.listdir(THUMBS_DIR):
        if not fname.endswith('.png'):
            continue
        filepath = os.path.join(THUMBS_DIR, fname)
        commit_time = get_file_commit_time(filepath)

        if commit_time < cutoff:
            try:
                os.remove(filepath)
                deleted += 1
            except Exception as e:
                print(f"[GitHub] Lỗi xóa {filepath}: {e}")
        else:
            kept += 1

    print(f"[GitHub] Xóa {deleted} ảnh cũ (>{days} ngày), giữ {kept} ảnh mới")
    return deleted

# ──────────────────────────────────────────────
# 2. Dọn ảnh cũ trên R2 (xóa qua S3 API)
# ──────────────────────────────────────────────
def cleanup_r2_thumbs(days=DAYS_TO_KEEP):
    # Kiểm tra có config R2 không (chạy trên local có thể không có)
    required_envs = ['R2_ENDPOINT', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'R2_BUCKET']
    if not all(os.environ.get(k) for k in required_envs):
        print("[R2] Thiếu config R2, bỏ qua dọn dẹp R2")
        return 0

    client = boto3.client(
        's3',
        endpoint_url=os.environ['R2_ENDPOINT'],
        aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],
        aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'],
        config=Config(signature_version='s3v4'),
        region_name='auto',
    )
    bucket = os.environ['R2_BUCKET']
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # List tất cả object trong prefix thumbs/
    to_delete = []
    kept = 0
    continuation_token = None
    while True:
        params = {'Bucket': bucket, 'Prefix': 'thumbs/'}
        if continuation_token:
            params['ContinuationToken'] = continuation_token
        resp = client.list_objects_v2(**params)

        for obj in resp.get('Contents', []):
            last_modified = obj['LastModified']  # đã có timezone
            if last_modified < cutoff:
                to_delete.append({'Key': obj['Key']})
            else:
                kept += 1

        if not resp.get('IsTruncated'):
            break
        continuation_token = resp.get('NextContinuationToken')

    if not to_delete:
        print(f"[R2] Không có ảnh nào cũ >{days} ngày, giữ {kept} ảnh")
        return 0

    # Xóa hàng loạt (R2 hỗ trợ delete_objects tối đa 1000 key/lần)
    deleted_total = 0
    for i in range(0, len(to_delete), 1000):
        batch = to_delete[i:i+1000]
        resp = client.delete_objects(
            Bucket=bucket,
            Delete={'Objects': batch, 'Quiet': True}
        )
        deleted_total += len(batch)
        # Báo lỗi nếu có
        for err in resp.get('Errors', []):
            print(f"[R2] Lỗi xóa {err.get('Key')}: {err.get('Message')}")

    print(f"[R2] Xóa {deleted_total} ảnh cũ (>{days} ngày), giữ {kept} ảnh")
    return deleted_total

# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
if __name__ == "__main__":
    print(f"=== Dọn dẹp ảnh thumbs cũ hơn {DAYS_TO_KEEP} ngày ===")
    gh_deleted = cleanup_github_thumbs(DAYS_TO_KEEP)
    r2_deleted = cleanup_r2_thumbs(DAYS_TO_KEEP)
    print(f"=== Tổng: GitHub xóa {gh_deleted}, R2 xóa {r2_deleted} ===")
