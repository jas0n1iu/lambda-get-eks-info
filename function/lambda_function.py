import os
import json
import boto3
import base64
import re
from botocore.signers import RequestSigner
from kubernetes import client, config

# 创建EKS客户端和S3客户端
eks_client = boto3.client('eks')
s3_client = boto3.client('s3')

# 定义要获取的信息类型
info_types = ['cluster_info', 'cluster_status', 'addons', 'ingress', 'nodegroups', 'kube_system_pods']

STS_TOKEN_EXPIRES_IN = 60
session = boto3.session.Session()
sts = session.client('sts')
service_id = sts.meta.service_model.service_id
#cluster_name = ''

def get_cluster_access_info(cluster_name):
    "Retrieve cluster endpoint and certificate"
    cluster_info = eks_client.describe_cluster(name=cluster_name)
    #endpoint = cluster_info['cluster']['endpoint']
    #cert_authority = cluster_info['cluster']['certificateAuthority']['data']
    cluster_info = {
        "endpoint" : cluster_info['cluster']['endpoint'],
        "ca" : cluster_info['cluster']['certificateAuthority']['data']
    }
    return cluster_info

def get_bearer_token(cluster_name):
    "Create authentication token"
    signer = RequestSigner(
        service_id,
        session.region_name,
        'sts',
        'v4',
        session.get_credentials(),
        session.events
    )

    params = {
        'method': 'GET',
        'url': 'https://sts.{}.amazonaws.com/'
               '?Action=GetCallerIdentity&Version=2011-06-15'.format(session.region_name),
        'body': {},
        'headers': {
            'x-k8s-aws-id': cluster_name
        },
        'context': {}
    }

    signed_url = signer.generate_presigned_url(
        params,
        region_name=session.region_name,
        expires_in=STS_TOKEN_EXPIRES_IN,
        operation_name=''
    )
    base64_url = base64.urlsafe_b64encode(signed_url.encode('utf-8')).decode('utf-8')

    # remove any base64 encoding padding:
    return 'k8s-aws-v1.' + re.sub(r'=*', '', base64_url)

def lambda_handler(event, context):
    # 获取所有EKS集群名称列表
    cluster_names = eks_client.list_clusters()['clusters']
    #cluster_names = ['demo-eks-cluster2']

    # 初始化一个字典来存储所有集群信息
    cluster_info = {"EKS_cluster": []}

    # 遍历集群名称列表
    for cluster_name in cluster_names:
        # 初始化一个字典来存储单个集群信息
        cluster_data = {}
        cluster_data['cluster_name'] = cluster_name

        # 获取集群配置信息
        if 'cluster_info' in info_types:
            response = eks_client.describe_cluster(name=cluster_name)
            cluster_data['version'] = response['cluster']['version']
            cluster_data['cluster_status'] = response['cluster']['status']
            

        # 获取节点组信息
        if 'nodegroups' in info_types:
            nodegroups = []
            response = eks_client.list_nodegroups(clusterName=cluster_name)
            for nodegroup in response['nodegroups']:
                nodegroup_info = eks_client.describe_nodegroup(
                    clusterName=cluster_name,
                    nodegroupName=nodegroup
                )

                is_gpu_node = 'gpu' in nodegroup_info['nodegroup']['amiType'].lower()

                nodegroup_data = {
                    'name': nodegroup,
                    'node_instance_type': nodegroup_info['nodegroup']['instanceTypes'],
                    'ami_type': nodegroup_info['nodegroup']['amiType'],
                    'is_gpu_node': is_gpu_node,
                    'version': nodegroup_info['nodegroup']['version']
                }
                nodegroups.append(nodegroup_data)
            cluster_data['nodegroups'] = nodegroups

        # 获取CSI、CNI等组件信息
        if 'addons' in info_types:
            addons = []
            response = eks_client.list_addons(clusterName=cluster_name)
            for addon in response['addons']:
                addon_info = eks_client.describe_addon(
                    clusterName=cluster_name,
                    addonName=addon
                )
                
                addon_data = {
                    'name': addon,
                    'version': addon_info['addon']['addonVersion'],
                    'status': addon_info['addon']['status']
                }
                addons.append(addon_data)
            cluster_data['addons'] = addons

        # 获取Ingress信息
        if 'ingress' in info_types:
            try:
                response = eks_client.describe_addon(
                    clusterName=cluster_name,
                    addonName='vpc-cni'
                )
                cluster_data['ingress'] = response['addon']['status']
            except eks_client.exceptions.ResourceNotFoundException:
                cluster_data['ingress'] = 'Ingress not found'

        # 获取kube-system命名空间的Pods信息
        if 'kube_system_pods' in info_types:
            cluster = get_cluster_access_info(cluster_name)
            kubeconfig = {
                'apiVersion': 'v1',
                'clusters': [{
                  'name': 'cluster1',
                  'cluster': {
                    'certificate-authority-data': cluster["ca"],
                    'server': cluster["endpoint"]}
                }],
                'contexts': [{'name': 'context1', 'context': {'cluster': 'cluster1', 'user': 'lambda'}}],
                'current-context': 'context1',
                'kind': 'Config',
                'preferences': {},
                'users': [{'name': 'lambda', 'user' : {'token': get_bearer_token(cluster_name)}}]
            }

            config.load_kube_config_from_dict(config_dict=kubeconfig)
            v1_api = client.CoreV1Api()
            pods = v1_api.list_namespaced_pod("kube-system")
            cluster_data['kube_system_pods'] = [pod.metadata.name for pod in pods.items]

        # 将单个集群信息添加到cluster_info字典中
        cluster_info["EKS_cluster"].append(cluster_data)

    bucket_name = os.environ.get('S3_BUCKET_NAME')
    object_key = 'eks-cluster-info/cluster_info.json'

    # 将cluster_info字典写入到S3 Bucket
    s3_client.put_object(
        Body=json.dumps(cluster_info, indent=2),
        Bucket=bucket_name,
        Key=object_key
    )
    print(f'所有集群信息已写入 S3 bucket: {bucket_name}/{object_key}')

    return {
        'statusCode': 200,
        'body': json.dumps('Lambda function executed successfully!')
    }