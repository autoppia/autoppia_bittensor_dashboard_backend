import requests
import random
import uuid
import string
from datetime import datetime, timedelta

websites = [
    "example.com",
    "google.com",
    "github.com",
    "bittensor.com",
    "sourcegraph.com",
    "medium.com",
    "twitter.com",
    "reddit.com",
    "wikipedia.org",
    "stackoverflow.com"
]

validator_uids = [2, 3, 4, 8, 13, 71, 120, 181, 224]

def generate_hotkey():
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(49))

miner_hotkey_map = {}
for uid in range(256):
    if uid not in validator_uids: 
        miner_hotkey_map[uid] = generate_hotkey()

for i in range(1000):
    validator_uid = random.choice(validator_uids)

    available_miner_uids = list(miner_hotkey_map.keys())
    miner_uid = random.choice(available_miner_uids)

    miner_hotkey = miner_hotkey_map[miner_uid]    

    task_id = str(uuid.uuid4())

    success = random.choice([True, False])

    score = round(random.uniform(0.0, 1.0), 2) if success else 0.0

    duration = random.randint(100, 5000)  

    website = random.choice(websites)
    
    random_days = random.randint(0, 30)
    random_seconds = random.randint(0, 86399)    
    base_date = datetime.now() - timedelta(days=random_days)
    base_date = base_date.replace(hour=0, minute=0, second=0, microsecond=0)
    created_at = base_date + timedelta(seconds=random_seconds)
    
    task_data = {
        "validator_uid": validator_uid,
        "miner_uid": miner_uid,
        "miner_hotkey": miner_hotkey,
        "task_id": task_id,
        "success": success,
        "score": score,
        "duration": duration,
        "website": website,
        "created_at": created_at.timestamp(),
    }

    try:
        response = requests.post("http://localhost:8000/tasks/", json=task_data)
        print(f"Task {i+1}/1000: Status {response.status_code} - {task_id}")
        if response.status_code != 201:
            print(f"Error: {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")

print("Data generation complete!")
