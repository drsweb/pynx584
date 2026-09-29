FROM python:3.14

ENV LISTEN=0.0.0.0

RUN pip install --no-cache-dir paho-mqtt
RUN pip install --no-cache-dir pynx584

EXPOSE 5007

ENTRYPOINT ["/usr/local/bin/nx584_server", "--listen", "0.0.0.0"]
CMD ["--help"]
