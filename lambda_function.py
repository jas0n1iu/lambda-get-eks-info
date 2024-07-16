import os
import json
import boto3
import base64
import re
import io
import openpyxl
from botocore.signers import RequestSigner
from kubernetes import client, config

STS_TOKEN_EXPIRES_IN = 60
session = boto3.session.Session()
sts = session.client('sts')
service_id = sts.meta.service_model.service_id

# 定义要检查的区域列表
regions = os.environ.get('REGIONS').split(',')

"""
def get_cluster_access_info(cluster_name, region):
    "Retrieve cluster endpoint and certificate"
    eks_client = boto3.client('eks', region_name=region)
    cluster_info = eks_client.describe_cluster(name=cluster_name)
    cluster_info = {
        "endpoint": cluster_info['cluster']['endpoint'],
        "ca": cluster_info['cluster']['certificateAuthority']['data']
    }
    return cluster_info
"""
def get_bearer_token(cluster_name, region):
    "Create authentication token"
    eks_client = boto3.client('eks', region_name=region)
    signer = RequestSigner(
        service_id,
        region,
        'sts',
        'v4',
        session.get_credentials(),
        session.events
    )

    params = {
        'method': 'GET',
        'url': 'https://sts.{}.amazonaws.com/'
               '?Action=GetCallerIdentity&Version=2011-06-15'.format(region),
        'body': {},
        'headers': {
            'x-k8s-aws-id': cluster_name
        },
        'context': {}
    }

    signed_url = signer.generate_presigned_url(
        params,
        region_name=region,
        expires_in=STS_TOKEN_EXPIRES_IN,
        operation_name=''
    )
    base64_url = base64.urlsafe_b64encode(signed_url.encode('utf-8')).decode('utf-8')

    # remove any base64 encoding padding:
    return 'k8s-aws-v1.' + re.sub(r'=*', '', base64_url)

def get_cluster_conf(eks_client, cluster_name,region):
    "Retrieve cluster endpoint and certificate"
    #eks_client = boto3.client('eks', region_name=region)
    cluster_info = eks_client.describe_cluster(name=cluster_name)

    kubeconfig = {
        'apiVersion': 'v1',
        'clusters': [{
            'name': 'cluster1',
            'cluster': {
                'certificate-authority-data': cluster_info['cluster']['certificateAuthority']['data'],
                'server': cluster_info['cluster']['endpoint']}
        }],
        'contexts': [{'name': 'context1', 'context': {'cluster': 'cluster1', 'user': 'lambda'}}],
        'current-context': 'context1',
        'kind': 'Config',
        'preferences': {},
        'users': [{'name': 'lambda', 'user': {'token': get_bearer_token(cluster_name, region)}}]
    }

    return kubeconfig

def lambda_handler(event, context):
    bucket_name = os.environ.get('S3_BUCKET_NAME')

    # 创建一个 Excel 工作簿
    workbook = openpyxl.Workbook()
    worksheet = workbook.active

    # 遍历每个区域
    for region in regions:
        eks_client = boto3.client('eks', region_name=region)

        # 获取所有EKS集群名称列表
        cluster_names = eks_client.list_clusters()['clusters']

        # 遍历集群名称列表
        for cluster_name in cluster_names:
            # 初始化一个字典来存储单个集群信息
            cluster_data = {}
            cluster_data['cluster_name'] = cluster_name
            cluster_data['region'] = region

            # 获取集群配置信息
            response = eks_client.describe_cluster(name=cluster_name)
            cluster_data['version'] = response['cluster']['version']
            cluster_data['cluster_status'] = response['cluster']['status']
            cluster_data['vpc'] = response['cluster']['resourcesVpcConfig']['vpcId']

            # 获取节点组信息
            nodegroups = []
            response = eks_client.list_nodegroups(clusterName=cluster_name)
            for nodegroup in response['nodegroups']:
                nodegroup_info = eks_client.describe_nodegroup(
                    clusterName=cluster_name,
                    nodegroupName=nodegroup
                )

                # 检查实例类型是否以 'g' 或 'p' 开头来判断是否为 GPU 节点
                instance_types = nodegroup_info['nodegroup']['instanceTypes']
                is_gpu_node = any(instance_type.startswith(('g', 'p')) for instance_type in instance_types)

                nodegroup_data = {
                    'name': nodegroup,
                    'node_instance_type': instance_types,
                    'ami_type': nodegroup_info['nodegroup']['amiType'],
                    'is_gpu_node': is_gpu_node,
                    'releaseVersion': nodegroup_info['nodegroup']['releaseVersion']
                }
                nodegroups.append(nodegroup_data)
            cluster_data['nodegroups'] = nodegroups

            # 获取CSI、CNI等组件信息
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
                    'status': addon_info['addon']['status'],
                    'service_account': addon_info['addon']['serviceAccountRoleArn'].split('/')[-1],
                    'addon_pods': ''
                }

                addons.append(addon_data)

            # 定义要检查的非AddOn Controller列表
            deployments = os.environ.get('ADDON_CONTROLLER').split(',')
            namespace = os.environ.get('NAMESPACE')
            #cluster = get_cluster_access_info(cluster_name, region)

            kubeconfig = get_cluster_conf(eks_client, cluster_name, region)

            config.load_kube_config_from_dict(config_dict=kubeconfig)
            v1_api = client.CoreV1Api()
            apps_api = client.AppsV1Api()

            for deployment in deployments:
                try:
                    deployment_info = apps_api.read_namespaced_deployment(deployment, namespace)

                    # 获取 Deployment 的 label selector
                    deployment_selector = deployment_info.spec.selector.match_labels

                    # 构建 label selector 字符串
                    label_selector_str = ','.join([f'{k}={v}' for k, v in deployment_selector.items()])

                    # 使用 label selector 获取 Deployment 关联的 Pod 列表
                    pod_list = v1_api.list_namespaced_pod(namespace, label_selector=label_selector_str)

                    # 从 Pod 列表中提取 Pod 名称
                    deployment_pods = [pod.metadata.name for pod in pod_list.items]
                    #deployment_pods = v1_api.list_namespaced_pod(namespace, label_selector=f"app.kubernetes.io/name={deployment}")

                    addon_data = {
                        'name': deployment,
                        'version': '',
                        'status': deployment_info.status.conditions[0].type,
                        'service_account': deployment_info.spec.template.spec.service_account_name,
                        'addon_pods': deployment_pods
                    }
                    addons.append(addon_data)
                except client.exceptions.ApiException as e:
                    if e.status == 404:
                        print(f'Deployment {deployment} not found in cluster {cluster_name}')
                    else:
                        raise

            cluster_data['addons'] = addons

            # 在工作簿中创建一个新的工作表
            #worksheet_cluster = workbook.create_sheet(title=f"{cluster_name} ({region})")

            # 写入集群信息表格
            headers_cluster = ['集群名称', '版本', 'VPC', 'Region']
            #worksheet_cluster.append(headers_cluster)
            worksheet.append(headers_cluster)
            row_cluster = [
                cluster_data['cluster_name'],
                cluster_data.get('version', ''),
                cluster_data.get('vpc', ''),
                cluster_data.get('region', '')
            ]
            #worksheet_cluster.append(row_cluster)
            #worksheet_cluster.append([])  # 添加一个空行
            worksheet.append(row_cluster)
            worksheet.append([])  # 添加一个空行

            # 写入 NodeGroup 表格
            headers_nodegroups = ['节点组名称', '版本', 'Node Instance Type', 'AMI Type', 'Is GPU Node']
            #worksheet_cluster.append(headers_nodegroups)
            worksheet.append(headers_nodegroups)
            for nodegroup in cluster_data.get('nodegroups', []):
                row_nodegroups = [
                    nodegroup['name'],
                    nodegroup['releaseVersion'],
                    ', '.join(nodegroup['node_instance_type']),
                    nodegroup['ami_type'],
                    nodegroup['is_gpu_node']
                ]
                #worksheet_cluster.append(row_nodegroups)
                worksheet.append(row_nodegroups)
            #worksheet_cluster.append([])  # 添加一个空行
            worksheet.append([])  # 添加一个空行

            # 写入 AddOn 表格
            headers_addons = ['AddOn名称', '版本', 'Status', 'Service Account', 'AddOn Pods']
            #worksheet_cluster.append(headers_addons)
            worksheet.append(headers_addons)
            for addon in cluster_data.get('addons', []):
                row_addons = [
                    addon['name'],
                    addon['version'],
                    addon['status'],
                    addon['service_account'],
                    '\n'.join(addon['addon_pods'])
                ]
                #worksheet_cluster.append(row_addons)
                worksheet.append(row_addons)
            #worksheet_cluster.append([])  # 添加一个空行
            worksheet.append([])  # 添加一个空行

    # 将 Excel 文件保存到内存中
    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)

    # 将 Excel 文件上传到 S3 Bucket
    object_key = 'eks-cluster-info/cluster_info_global.xlsx'
    s3_client = boto3.client('s3')
    s3_client.upload_fileobj(output, bucket_name, object_key)
    print(f'集群信息表格已保存到 S3 bucket: {bucket_name}/{object_key}')

    return {
        'statusCode': 200,
        'body': json.dumps('Lambda function executed successfully!')
    }