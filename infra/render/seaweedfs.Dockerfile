# SeaweedFS for render.yaml: the image docker-compose.yml runs, started the
# same way, but its S3 key comes from STORAGE_ACCESS_KEY and STORAGE_SECRET_KEY
# (seaweedfs-start.sh), never from infra/seaweedfs/s3.json, whose key is a
# laptop placeholder. The build context is infra/ (render.yaml, dockerContext).
FROM chrislusf/seaweedfs:4.48
COPY render/seaweedfs-start.sh /render-start.sh
ENTRYPOINT ["/bin/sh", "/render-start.sh"]
