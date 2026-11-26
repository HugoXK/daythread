FROM python:3.12-slim

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir -e ".[crypto]"

ENV DAYTHREAD_HOME=/data
RUN mkdir -p /data && chmod 700 /data

EXPOSE 8765 8788
VOLUME ["/data"]

ENTRYPOINT ["dt"]
CMD ["doctor"]
