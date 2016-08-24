import boto3
from botocore.client import Config
import botocore
import sys
import os
import ConfigParser
import glob
import zipfile
import time

def main():
    if len(sys.argv) < 3:
        print "-------------------------------------------------------------"
        print "ilaws -- a wrapper for AWS for launching ilastik segmentation on the cloud"
        print "usage: python ilaws.py <path to directory with input data> <path to ilastik project file>"
        print "written by michael morehead @ ilastik workshop 2016"
        print "Adjusted to use SQS by Carsten Haubold, 2016 "
        print "-------------------------------------------------------------"
        sys.exit()
    folderPath = sys.argv[1]
    projectPath = sys.argv[2]
    config = ConfigParser.ConfigParser()
    config.read("./config.ini")
    bucketName = config.get('info', 'bucket')

    print("Setting up connection")
    conn_args = {
        'aws_access_key_id': config.get('info', 'aws_access_key_id'),
        'aws_secret_access_key': config.get('info', 'aws_secret_access_key'),
        'region_name': config.get('info', 'region_name')
    }

    # messaging queues: set up and remove all previous messages
    sqs = boto3.resource('sqs', **conn_args)
    taskQueue = sqs.get_queue_by_name(QueueName='ilastik-task-queue')
    finishedQueue = sqs.get_queue_by_name(QueueName='ilastik-finished-queue')
    try:
        taskQueue.purge()
        finishedQueue.purge()
    except botocore.exception.ClientError:
        print("Could not purge message queues, might not have waited 60 seconds in between")
    
    s3 = boto3.client('s3', config=Config(signature_version='s3v4'), **conn_args)

    # upload ilastik project file as zip
    if not projectPath.endswith('.zip'):
        print("Zipping ilastik project file")
        with zipfile.ZipFile(projectPath+'.zip', 'w') as z:
            z.write(projectPath)
        projectPath += '.zip'
    print("Uploading ilastik project file")
    s3.upload_file(projectPath, bucketName, "ilastik-project-zip")
    
    filesToProcess = glob.glob(os.path.join(folderPath, "*"))
    remainingKeys = []

    # dispatch work
    print("Dispatching jobs:")
    for index, fileFullPath in enumerate(filesToProcess):
        if not os.path.isfile(fileFullPath):
            print("Skipping {}, is no file".format(fileFullPath))
            continue
        
        # upload data and project file
        filename = os.path.basename(fileFullPath)
        fileKey = "image-{}".format(index)
        print("Uploading {} to {}:{}".format(filename, bucketName, fileKey))
        s3.upload_file(fileFullPath, bucketName, fileKey)
        

        # send message about task:
        taskQueue.send_message(MessageBody=filename, MessageAttributes={
            'ilp-key': {
                'StringValue':'ilastik-project-zip', 
                'DataType': 'String'
            }, 
            'file-key': {
                'StringValue':fileKey,
                'DataType': 'String'
            }
        })
        
        remainingKeys.append(fileKey)
    print("\n*********************\nDone dispatching all tasks\n*********************\n")

    # wait for all results:
    try:
        print("Waiting for results")
        while len(remainingKeys) > 0:
            for message in finishedQueue.receive_messages(MessageAttributeNames=['result-key', 'file-key'], MaxNumberOfMessages=1):
                # Get the custom author message attribute if it was set
                if message.message_attributes is not None:
                    resultFileKey = message.message_attributes.get('result-key').get('StringValue')
                    inputFileKey = message.message_attributes.get('file-key').get('StringValue')
                else:
                    print("Got unknown message {}".format(message))

                filename = message.body
                print("Got result for {} = {}, downloading...".format(message.body, inputFileKey))

                # # download file and remove from s3
                # try:
                #     s3.download_file(bucketName, resultFileKey, 'result_' + filename)
                #     s3.delete_object(Bucket=bucketName, Key=resultFileKey)
                #     message.delete()
                # except botocore.exceptions.ClientError:
                #     print("Could not find result file {} to download".format(filename))
                #     message.delete()
                #     continue

                assert(inputFileKey in remainingKeys)
                remainingKeys.remove(inputFileKey)

    except KeyboardInterrupt:
        print("WARNING: not all results have been fetched yet, but will still be computed, and the results will be stored in S3")
    
    print("Done!")

if __name__ == "__main__":
    main()
