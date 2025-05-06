import csv
import random
import logging
import os
import duckdb
import uuid

from faker import Faker
from datetime import date, datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator


# Configure logging.
logging.basicConfig(
    level=logging.INFO,                    
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler()]
)


def _create_data(locale: str) -> Faker:
    """
    Creates a Faker instance for generating localized fake data.
    Args:
        locale (str): The locale code for the desired fake data language/region.
    Returns:
        Faker: An instance of the Faker class configured with the specified locale.
    """
    # Log the action.
    logging.info(f"Created synthetic data for {locale.split('_')[-1]} country code.")
    return Faker(locale)


def _generate_record(fake: Faker) -> list:
    """
    Generates a single fake user record.
    Args:
        fake (Faker): A Faker instance for generating random data.
    Returns:
        list: A list containing various fake user details such as name, username, email, etc.
    """
    # Generate random personal data.
    person_name = fake.name()
    user_name = person_name.replace(" ", "").lower()  # Create a lowercase username without spaces.
    email = f"{user_name}@{fake.free_email_domain()}"  # Combine the username with a random email domain.
    personal_number = fake.ssn()  # Generate a random social security number.
    birth_date = fake.date_of_birth()  # Generate a random birth date.
    address = fake.address().replace("\n", ", ")  # Replace newlines in the address with commas.
    phone_number = fake.phone_number()  # Generate a random phone number.
    mac_address = fake.mac_address()  # Generate a random MAC address.
    ip_address = fake.ipv4()  # Generate a random IPv4 address.
    iban = fake.iban()  # Generate a random IBAN.
    accessed_at = fake.date_time_between("-1y")  # Generate a random date within the last year.
    session_duration = random.randint(0, 36_000)  # Random session duration in seconds (up to 10 hours).
    download_speed = random.randint(0, 1_000)  # Random download speed in Mbps.
    upload_speed = random.randint(0, 800)  # Random upload speed in Mbps.
    consumed_traffic = random.randint(0, 2_000_000)  # Random consumed traffic in kB.

    # Return all the generated data as a list.
    return [
        person_name, user_name, email, personal_number, birth_date,
        address, phone_number, mac_address, ip_address, iban, accessed_at,
        session_duration, download_speed, upload_speed, consumed_traffic
    ]


def _write_to_duckdb():
    """
    Generates multiple fake user records and stores them in DuckDB.
    Handles both initial and subsequent loads.
    """
    fake = _create_data("ro_RO")
    
    # Establish number of rows based on date
    if str(date.today()) == "2025-05-05":
        rows = random.randint(100_372, 100_372)
    else:
        rows = random.randint(0, 1_101)
    
    # Connect to DuckDB 
    conn = duckdb.connect('/opt/airflow/data/raw_data.duckdb')
    
    # Create table 
    conn.execute("""
    CREATE TABLE IF NOT EXISTS raw_data (
        person_name VARCHAR,
        user_name VARCHAR,
        email VARCHAR,
        personal_number VARCHAR,
        birth_date DATE,
        address VARCHAR,
        phone VARCHAR,
        mac_address VARCHAR,
        ip_address VARCHAR,
        iban VARCHAR,
        accessed_at TIMESTAMP,
        session_duration INTEGER,
        download_speed INTEGER,
        upload_speed INTEGER,
        consumed_traffic INTEGER,
        unique_id VARCHAR,
        is_current BOOLEAN DEFAULT TRUE
    )
    """)
    
    # For incremental loads, mark previous records as not current
    conn.execute("UPDATE raw_data SET is_current = FALSE")
    
    # Generate and insert new records
    new_records = [_generate_record(fake) for _ in range(rows)]
    
    # Add unique_id during insertion
    for record in new_records:
        record_uuid = str(uuid.uuid4())
        full_record = (*record, record_uuid, True)
        conn.execute("""
        INSERT INTO raw_data VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, full_record)
    
    logging.info(f"Added {rows} new records.")

def _update_datetime() -> None:
    """
    Update the 'accessed_at' column only for new records in the current batch.
    Export all current records to CSV.
    """
    conn = duckdb.connect('/opt/airflow/data/raw_data.duckdb')
    
    # For subsequent runs (not initial load)
    if str(date.today()) != "2025-05-05":
        current_time = datetime.now().replace(microsecond=0)
        yesterday_time = str(current_time - timedelta(days=1))
        
        # Update only records from the current batch
        conn.execute(f"""
        UPDATE raw_data 
        SET accessed_at = '{yesterday_time}'
        WHERE is_current = TRUE
        """)
    
    # Export only current records to CSV
    conn.execute("""
    COPY (SELECT * FROM raw_data WHERE is_current = TRUE) 
    TO '/opt/airflow/data/raw_data.csv' 
    WITH (HEADER, DELIMITER ',')
    """)
    logging.info("Exported current data to CSV")



def save_raw_data():
    '''
    Execute all steps for data generation.
    '''
    # Logging starting of the process.
    logging.info(f"Started batch processing for {date.today()}.")
    # Generate and write records to the DuckDB and add uuid
    _write_to_duckdb()
    # Update the timestamp and export to csv
    _update_datetime()
    # Logging ending of the process.
    logging.info(f"Finished batch processing {date.today()}.")


# Define the default arguments for DAG.
default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'retries': 0,
}

# Define the DAG.
dag = DAG(
    'extract_raw_data_pipeline2',
    default_args=default_args,
    description='LeadData Main Pipeline.',
    schedule_interval="@daily",
    start_date=datetime(2025, 5, 5),
    catchup=False,
)

# Define extract raw data task.
extract_raw_data_task = PythonOperator(
    task_id='extract_raw_data',
    python_callable=save_raw_data,
    dag=dag,
)

# Define create raw schema task.
create_raw_schema_task = SQLExecuteQueryOperator(
    task_id='create_raw_schema',
    conn_id='postgres_conn',
    sql='CREATE SCHEMA IF NOT EXISTS lead_raw;',
    dag=dag,
)

# Define create raw table task.
create_raw_table_task = SQLExecuteQueryOperator(
    task_id='create_raw_table',
    conn_id='postgres_conn',
    sql="""
        CREATE TABLE IF NOT EXISTS lead_raw.raw_batch_data (
            person_name VARCHAR(100),
            user_name VARCHAR(100),
            email VARCHAR(100),
            personal_number VARCHAR(100), 
            birth_date VARCHAR(100), 
            address VARCHAR(100),
            phone VARCHAR(100), 
            mac_address VARCHAR(100),
            ip_address VARCHAR(100),
            iban VARCHAR(100),
            accessed_at TIMESTAMP,
            session_duration INT,
            download_speed INT,
            upload_speed INT,
            consumed_traffic INT,
            unique_id VARCHAR(100),
            is_current BOOLEAN DEFAULT TRUE
        );
    """,
    dag=dag
)

# Define load CSV data into the table task.
load_raw_data_task = SQLExecuteQueryOperator(
    task_id='load_raw_data',
    conn_id='postgres_conn',
    sql="""
    COPY lead_raw.raw_batch_data(
    person_name, user_name, email, personal_number, birth_date,
    address, phone, mac_address, ip_address, iban, accessed_at,
    session_duration, download_speed, upload_speed, consumed_traffic, unique_id, is_current
    ) 
    FROM '/opt/airflow/data/raw_data.csv' 
    DELIMITER ',' 
    CSV HEADER;
    """
)

# Define staging dbt models run task.
run_dbt_staging_task = BashOperator(
    task_id='run_dbt_staging',
    bash_command='set -x; cd /opt/airflow/dbt && dbt run --select tag:staging',
)

# Define trusted dbt models run task.
run_dbt_trusted_task = BashOperator(
    task_id='run_dbt_trusted',
    bash_command='set -x; cd /opt/airflow/dbt && dbt run --select tag:trusted',
)

# Set the task in the DAG
[extract_raw_data_task, create_raw_schema_task] >> create_raw_table_task
create_raw_table_task >> load_raw_data_task >> run_dbt_staging_task
run_dbt_staging_task >> run_dbt_trusted_task