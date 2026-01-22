from datetime import datetime, timedelta
import yaml

from airflow import DAG
from airflow.operators.empty import EmptyOperator
from airflow.providers.cncf.kubernetes.operators.kubernetes import KubernetesManifestOperator


DEFAULT_ARGS = {
    "owner": "data-platform",
    "depends_on_past": False,
    "retries": 0,
}


SPARK_APP_NAME = "airflow-spark-inline-test"


def build_spark_application_manifest():
    spark_app = {
        "apiVersion": "sparkoperator.k8s.io/v1beta2",
        "kind": "SparkApplication",
        "metadata": {
            "name": SPARK_APP_NAME,
            "namespace": "spark",   # namespace du Spark Operator
        },
        "spec": {
            "type": "Python",
            "mode": "cluster",
            "image": "gcr.io/spark-operator/spark-py:v3.4.1",
            "imagePullPolicy": "IfNotPresent",

            # Script Python inline monté via ConfigMap-like trick
            "mainApplicationFile": "local:///opt/spark/work-dir/inline_job.py",

            "sparkVersion": "3.4.1",
            "restartPolicy": {
                "type": "Never"
            },

            "driver": {
                "cores": 1,
                "coreLimit": "1200m",
                "memory": "1g",
                "serviceAccount": "spark-sa",
                "labels": {"version": "3.4.1"},
            },

            "executor": {
                "cores": 1,
                "instances": 2,
                "memory": "1g",
                "labels": {"version": "3.4.1"},
            },

            "sparkConf": {
                "spark.sql.shuffle.partitions": "4",
                "spark.default.parallelism": "4",
            },

            # Script injecté comme configMap inline via volumes
            "volumes": [
                {
                    "name": "inline-script",
                    "configMap": {
                        "name": "spark-inline-script-cm"
                    }
                }
            ],
            "driverVolumeMounts": [
                {
                    "name": "inline-script",
                    "mountPath": "/opt/spark/work-dir"
                }
            ],
            "executorVolumeMounts": [
                {
                    "name": "inline-script",
                    "mountPath": "/opt/spark/work-dir"
                }
            ],
        }
    }

    return spark_app


with DAG(
    dag_id="spark_operator_inline_test_dag",
    default_args=DEFAULT_ARGS,
    description="Spark inline test via Spark Operator (no I/O, no packaging)",
    start_date=datetime(2025, 1, 1),
    schedule_interval=None,
    catchup=False,
    max_active_runs=1,
    tags=["spark", "k8s", "operator", "test"],
) as dag:

    start = EmptyOperator(task_id="start")

    apply_spark_application = KubernetesManifestOperator(
        task_id="submit_spark_application",
        manifest=build_spark_application_manifest(),
        kubernetes_conn_id="kubernetes_default",
    )

    end = EmptyOperator(task_id="end")

    start >> apply_spark_application >> end
