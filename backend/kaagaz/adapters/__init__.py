"""The real outside systems behind the ports: S3 storage, the SQS queue and the
clamd virus scanner. The same code runs against the laptop stand-ins
(SeaweedFS, ElasticMQ, ClamAV in docker-compose.yml) and AWS; only settings
change."""
