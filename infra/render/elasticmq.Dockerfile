# ElasticMQ for render.yaml: the image docker-compose.yml runs, with the same
# config. Render cannot mount a file into a service, so it is copied in.
# The build context is infra/ (render.yaml, dockerContext).
FROM softwaremill/elasticmq-native:1.7.1
COPY elasticmq/elasticmq.conf /opt/elasticmq.conf
