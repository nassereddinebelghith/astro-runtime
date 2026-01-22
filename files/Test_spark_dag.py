from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.empty import EmptyOperator
from airflow.operators.bash import BashOperator


DEFAULT_ARGS = {
    "owner": "data-platform",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=2),
}


with DAG(
    dag_id="spark_inline_test_dag",
    default_args=DEFAULT_ARGS,
    description="Minimal DAG to validate Spark execution from Airflow (inline job, no I/O)",
    start_date=datetime(2025, 1, 1),
    schedule_interval=None,
    catchup=False,
    max_active_runs=1,
    tags=["spark", "test", "inline"],
) as dag:

    start = EmptyOperator(task_id="start")

    spark_inline_test = BashOperator(
        task_id="run_spark_inline_job",
        pool="spark_pool",        # protège ton cluster
        env={
            "ENV": "dev",
            "JOB_NAME": "airflow_spark_inline_test",
        },
        bash_command="""
set -euo pipefail

echo "=== Submitting inline Spark job ==="

spark-submit \
  --name airflow-spark-inline-test \
  --master spark://spark-master:7077 \
  --deploy-mode client \
  --executor-cores 2 \
  --executor-memory 2g \
  --driver-memory 1g \
  --num-executors 2 \
  --conf spark.sql.shuffle.partitions=4 \
  --conf spark.default.parallelism=4 \
  - << 'EOF'
from pyspark.sql import SparkSession

spark = SparkSession.builder \
    .appName("airflow_spark_inline_test") \
    .getOrCreate()

data = [
    (1, "Alice", 100),
    (2, "Bob", 200),
    (3, "Charlie", 300),
]

df = spark.createDataFrame(data, ["id", "name", "amount"])

df_transformed = df.withColumn("amount_x2", df["amount"] * 2)

print("=== Input DataFrame ===")
df.show(truncate=False)

print("=== Transformed DataFrame ===")
df_transformed.show(truncate=False)

print("=== Spark inline test job completed successfully ===")

spark.stop()
EOF

echo "=== spark-submit finished ==="
"""
    )

    end = EmptyOperator(task_id="end")

    start >> spark_inline_test >> end
