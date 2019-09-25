#!/usr/bin/env python3
# start the builder node
from boto3.session import Session
import argparse

parser = argparse.ArgumentParser()
parser.add_argument('--instance_id', help='instance id')
parser.add_argument('--region', help='region')
parser.add_argument('--stop', help='stop the instance')
parser.add_argument('--user', help='ec2 ssh username')
parser.add_argument('--pem', help='.pem file location for ssh')
args = parser.parse_args()

sess = Session(region_name=args.region)
ec2 = sess.client('ec2')
response = ec2.describe_instances()
if args.instance_id:
    instance_id = args.instance_id
else:
    instance_id = response['Reservations'][0]['Instances'][0]['InstanceId']

if args.stop:
    ec2.stop_instances(InstanceIds=[instance_id])
else:
    ec2.start_instances(InstanceIds=[instance_id])

waiter = ec2.get_waiter('instance_running')
waiter.wait(InstanceIds=[instance_id])
new_response = ec2.describe_instances()

print(f"ssh -i {args.pem} {args.user}@{new_response['Reservations'][0]['Instances'][0]['PublicDnsName']}")
