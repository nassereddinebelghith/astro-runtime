"""
End-to-end integration tests for Airflow + Spark using pytest.

Run:
    pip install pytest requests pyspark
    export AIRFLOW_BASE_URL=https://airflow.example.com
    export AIRFLOW_USERNAME=test_user
    export AIRFLOW_PASSWORD=secret
    pytest -v test_integration_all.py
"""

import os
import time
import pytest
import requests
from pyspark.sql import SparkSession, Row


# -------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------

AIRFLOW_BASE_URL = os.getenv("AIRFLOW_BASE_URL", "https://airflow.example.com")
AIRFLOW_USERNAME = os.getenv("AIRFLOW_USERNAME", "test_user")
AIRFLOW_PASSWORD = os.getenv("AIRFLOW_PASSWORD", "test_password")
DAG_ID = os.getenv("AIRFLOW_TEST_DAG_ID", "example_dag")


# -------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------

@pytest.fixture(scope="session")
def airflow_session():
    """
    Create an authenticated HTTP session against Airflow.
    Adapt login flow if using OAuth redirect.
    """
    session = requests.Session()

    # Basic form login example (adjust to your auth flow)
    resp = session.post(
        f"{AIRFLOW_BASE_URL}/login",
        data={
            "username": AIRFLOW_USERNAME,
            "password": AIRFLOW_PASSWORD,
        },
        allow_redirects=True,
        timeout=15,
    )

    assert resp.status_code in (200, 302)
    return session


@pytest.fixture(scope="session")
def spark():
    """
    Local Spark session for integration tests.
    """
    spark = (
        SparkSession.builder
        .master("local[*]")
        .appName("pytest-airflow-spark")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )

    yield spark
    spark.stop()


# -------------------------------------------------------------------
# Spark logic (example)
# -------------------------------------------------------------------

def spark_transform(df):
    """
    Example Spark transformation logic.
    """
    return df.filter(df.value > 10)


# -------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------

def test_airflow_health(airflow_session):
    """
    Verify Airflow API is reachable and healthy.
    """
    resp = airflow_session.get(
        f"{AIRFLOW_BASE_URL}/api/v2/health",
        timeout=10,
    )

    assert resp.status_code == 200
    assert resp.json()["metadatabase"]["status"] == "healthy"


def test_airflow_dag_run(airflow_session):
    """
    Trigger a DAG and wait until it finishes successfully.
    """
    run_id = f"pytest_run_{int(time.time())}"

    # Trigger DAG
    resp = airflow_session.post(
        f"{AIRFLOW_BASE_URL}/api/v2/dags/{DAG_ID}/dagRuns",
        json={"dag_run_id": run_id},
        timeout=10,
    )
    assert resp.status_code == 200

    # Poll DAG state
    for _ in range(30):
        status_resp = airflow_session.get(
            f"{AIRFLOW_BASE_URL}/api/v2/dags/{DAG_ID}/dagRuns/{run_id}",
            timeout=10,
        )
        assert status_resp.status_code == 200

        state = status_resp.json()["state"]

        if state == "success":
            return

        if state == "failed":
            pytest.fail("DAG run failed")

        time.sleep(10)

    pytest.fail("DAG run did not complete in time")


def test_spark_transformation(spark):
    """
    Validate Spark transformation logic locally.
    """
    data = [
        Row(id=1, value=5),
        Row(id=2, value=20),
        Row(id=3, value=30),
    ]

    df = spark.createDataFrame(data)
    result = spark_transform(df)

    assert result.count() == 2
    assert {row.id for row in result.collect()} == {2, 3}