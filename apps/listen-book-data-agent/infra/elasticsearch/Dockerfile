FROM elasticsearch:8.19.10

USER root

COPY plugins/elasticsearch-analysis-ik-8.19.10.zip /tmp/elasticsearch-analysis-ik.zip

RUN /usr/share/elasticsearch/bin/elasticsearch-plugin install --batch \
    file:///tmp/elasticsearch-analysis-ik.zip \
    && rm /tmp/elasticsearch-analysis-ik.zip \
    && chown -R elasticsearch:elasticsearch /usr/share/elasticsearch/plugins

USER elasticsearch
