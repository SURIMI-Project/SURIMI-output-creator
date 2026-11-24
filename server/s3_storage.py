import logging
import os
from pathlib import Path
import boto3

class S3_Storage:
    @staticmethod
    def UploadFilesToS3(source_directory, destination_directory):
        """
        Upload NetCDF output files from a local experiment directory to S3.

        Only files with a .nc extension are uploaded. This intentionally skips
        recorder artifacts such as .bin files.

        Returns:
            bool: True when at least one .nc file was uploaded successfully,
            otherwise False.
        """

        if not os.environ.get('AWS_BUCKET_NAME'):
            logging.error("AWS_BUCKET_NAME environment variable not set. Aborting S3 upload.")
            return False

        if not os.environ.get('AWS_ACCESS_KEY_ID'):
            logging.error("AWS_ACCESS_KEY_ID environment variable not set. Aborting S3 upload.")
            return False

        bucket_name = os.environ.get('AWS_BUCKET_NAME') 

        logging.info(f"Uploading files from {source_directory} to S3...")
        s3 = boto3.client(
            's3',
            aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
            region_name=os.environ.get('AWS_DEFAULT_REGION'),
            endpoint_url= "https://" + os.environ.get('AWS_S3_ENDPOINT')
        )

        logging.info(f"Using S3 bucket: {bucket_name} and S3 directory: {destination_directory} and local output directory: {source_directory}")

        uploaded_file_count = 0
        try:
            for root, dirs, files in os.walk(source_directory):
                for file in files:
                    # Only publish the final NetCDF output to object storage.
                    if not file.endswith(".nc"):
                        continue

                    logging.info(f"Uploading {file} from {root} to S3...")
                    local_path = Path(root) / file
                    # Compute the S3 key by replacing the local_sim_dir part with s3_sim_dir
                    relative_path = local_path.relative_to(source_directory)
                    s3_key = f"{destination_directory}/{relative_path.as_posix()}"
                    logging.info(f"Upload {local_path} to s3://{bucket_name}/{s3_key}")
                    s3.upload_file(str(local_path), bucket_name, s3_key)
                    uploaded_file_count += 1

            if uploaded_file_count == 0:
                logging.error(f"No NetCDF (.nc) files found in {source_directory}. Nothing was uploaded to S3.")
                return False

            logging.info(f"Uploaded {uploaded_file_count} NetCDF file(s) to s3://{bucket_name}/{destination_directory}")
            return True
        except Exception as e:
            logging.error(f"Error uploading files to S3: {e}")
            return False

    @staticmethod
    def DownloadFilesFromS3(source_directory, destination_directory):
        """
        Download all files from the given S3 source_directory (prefix) to the local destination_directory.
        """
        if not os.environ.get('AWS_BUCKET_NAME'):
            logging.error("AWS_BUCKET_NAME environment variable not set. Aborting S3 download.")
            return

        bucket_name = os.environ.get('AWS_BUCKET_NAME')

        logging.info(f"Downloading files from s3://{bucket_name}/{source_directory} to {destination_directory}...")
        s3 = boto3.client(
            's3',
            aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
            aws_session_token=os.environ.get('AWS_SESSION_TOKEN'),
            region_name=os.environ.get('AWS_DEFAULT_REGION'),
            endpoint_url="https://" + os.environ.get('AWS_S3_ENDPOINT')
        )

        paginator = s3.get_paginator('list_objects_v2')
        try:
            for page in paginator.paginate(Bucket=bucket_name, Prefix=source_directory):
                for obj in page.get('Contents', []):
                    s3_key = obj['Key']
                    # Skip directories
                    if s3_key.endswith('/'):
                        continue
                    # Skip .keep files. They are used to keep empty directories in S3. (directories may not be empty in S3)
                    if s3_key.endswith('.keep'):
                        continue                    
                    # Compute local file path
                    relative_path = Path(s3_key).relative_to(source_directory)
                    local_path = Path(destination_directory) / relative_path
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    logging.info(f"Downloading {s3_key} to {local_path}")
                    s3.download_file(bucket_name, s3_key, str(local_path))
        except Exception as e:
            logging.error(f"Error downloading files from S3: {e}")