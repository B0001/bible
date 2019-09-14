#!/bin/bash
# from https://medium.com/faun/apache-spark-on-kubernetes-docker-for-mac-2501cc72e659
wget http://www.trieuvan.com/apache/spark/spark-2.4.4/spark-2.4.4-bin-hadoop2.7.tgz
tar xvzf spark-2.4.4-bin-hadoop2.7.tgz
cd spark-2.4.4-bin-hadoop2.7
./bin/docker-image-tool.sh -t spark-docker build
kubectl create serviceaccount spark
kubectl create clusterrolebinding spark-role --clusterrole=edit  --serviceaccount=default:spark --namespace=default

bin/spark-submit  \
    --master k8s://https://localhost:6443  \
    --deploy-mode cluster  \
    --conf spark.executor.instances=1  \
    --conf spark.kubernetes.authenticate.driver.serviceAccountName=spark  \
    --conf spark.kubernetes.container.image=spark:spark-docker  \
    --class org.apache.spark.examples.SparkPi  \
    --name spark-pi  \
    local:///Users/x/spark-2.4.4-bin-hadoop2.7/examples/jars/spark-examples_2.11-2.4.4.jar
