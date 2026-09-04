"""boto3 S3 client factory for OOS (Outscale Object Storage) accounts."""
import boto3
from botocore.config import Config

from . import storage


def get_client(account):
    secret_key = storage.decrypt_secret(account["secret_key_enc"])
    endpoint_url = f"https://oos.{account['region']}.outscale.com"
    return boto3.client(
        "s3",
        aws_access_key_id=account["access_key"],
        aws_secret_access_key=secret_key,
        endpoint_url=endpoint_url,
        region_name=account["region"],
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )
