import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from typing import Optional
import uuid
from app.config import settings


class R2Storage:
    def __init__(self):
        self.s3_client = boto3.client(
            's3',
            endpoint_url=settings.R2_ENDPOINT_URL,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            config=Config(signature_version='s3v4'),
            region_name='auto'
        )
        self.bucket_name = settings.R2_BUCKET_NAME
        self.public_url = settings.R2_PUBLIC_URL

    async def upload_file(self, file_data: bytes, filename: str, content_type: str) -> str:
        """
        Upload file to R2 and return the public URL
        """
        try:
            # Generate unique filename
            unique_filename = f"{uuid.uuid4()}_{filename}"
            
            # Upload to R2
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=unique_filename,
                Body=file_data,
                ContentType=content_type
            )
            
            # Return public URL
            file_url = f"{self.public_url}/{unique_filename}"
            return file_url
        except ClientError as e:
            raise Exception(f"Failed to upload file to R2")

    async def delete_file(self, file_url: str) -> bool:
        """
        Delete file from R2 given its public URL
        """
        try:
            # Extract filename from URL
            filename = file_url.split('/')[-1]
            
            # Delete from R2
            self.s3_client.delete_object(
                Bucket=self.bucket_name,
                Key=filename
            )
            return True
        except ClientError as e:
            # Don't raise error on delete failure
            return False


r2_storage = R2Storage()

