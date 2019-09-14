FROM spark-py:spark-docker

WORKDIR /app
  
RUN pip install	dash

WORKDIR /app
COPY dash_app.py /app


# FROM gcr.io/distroless/python3
# COPY --from=build-env /app /app
# WORKDIR /app
# CMD ["dash_app.py"]