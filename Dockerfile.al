FROM amazonlinux

RUN yum install -y clang
ENV CC=/usr/bin/clang \
    CXX=/usr/bin/clang++
RUN yum install -y python3-devel
RUN python3 -m pip install --upgrade pip && \
    python3 -m pip install \
    	dash \
    	fastparquet \
        pandas
RUN python3 -m pip install s3fs

WORKDIR /app
COPY dash_app.py /app
