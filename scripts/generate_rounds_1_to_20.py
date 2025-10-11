#!/usr/bin/env python3
"""
Generate rounds 1-20 for the AutoPPIA dashboard mock data.
"""
import json
import random
import time
from datetime import datetime, timedelta

def generate_rounds_1_to_20():
    """Generate rounds 1-20 with realistic data."""
    rounds = []
    
    # Base timestamp for round 1 (20 days ago)
    base_timestamp = time.time() - (20 * 24 * 60 * 60)  # 20 days ago
    
    for round_num in range(1, 21):
        # Calculate timestamps for this round
        round_start = base_timestamp + (round_num - 1) * (24 * 60 * 60)  # 1 day per round
        round_end = round_start + random.randint(1800, 3600)  # 30-60 minutes duration
        
        # Generate realistic data
        n_tasks = random.randint(8, 15)
        n_miners = random.randint(3, 8)
        n_winners = random.randint(2, min(n_miners, 5))
        
        # Generate winners with scores
        winners = []
        for i in range(n_winners):
            score = round(random.uniform(0.6, 0.95), 3)
            winners.append({
                "miner_uid": random.randint(1, 10),
                "score": score,
                "rank": i + 1,
                "reward": round(score * 100, 2)
            })
        
        # Sort winners by score (descending)
        winners.sort(key=lambda x: x["score"], reverse=True)
        
        # Generate miners list
        miners = []
        for i in range(n_miners):
            miners.append({
                "uid": random.randint(1, 10),
                "hotkey": f"5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY{i:02d}",
                "coldkey": None,
                "agent_name": f"Agent {i+1}",
                "agent_image": f"/agents/agent_{i+1}.png",
                "github": f"https://github.com/agent{i+1}/autoppia-agent"
            })
        
        # Create round data
        round_data = {
            "round_id": f"round_{round_num:03d}",
            "validator_info": {
                "uid": 123,
                "hotkey": "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
                "coldkey": None,
                "stake": 1000.0,
                "vtrust": round(random.uniform(0.8, 1.0), 2)
            },
            "start_block": 1000 + (round_num * 100),
            "start_epoch": round_num,
            "end_block": 1000 + (round_num * 100) + random.randint(50, 200),
            "end_epoch": round_num + 1,
            "started_at": round_start,
            "ended_at": round_end,
            "elapsed_sec": round_end - round_start,
            "max_epochs": 20,
            "max_blocks": 360,
            "n_tasks": n_tasks,
            "n_miners": n_miners,
            "n_winners": n_winners,
            "miners": miners,
            "winners": winners,
            "winner_scores": [w["score"] for w in winners],
            "weights": {miner["uid"]: round(random.uniform(0.1, 1.0), 3) for miner in miners}
        }
        
        rounds.append(round_data)
    
    return rounds

def main():
    """Generate and save rounds 1-20."""
    print("Generating rounds 1-20...")
    
    # Generate rounds
    rounds = generate_rounds_1_to_20()
    
    # Save to file
    output_file = "/home/usuario1/autoppia/autoppia_bittensor_dashboard_backend/data/mock/rounds_1_to_20.json"
    with open(output_file, 'w') as f:
        json.dump(rounds, f, indent=2)
    
    print(f"✅ Generated {len(rounds)} rounds and saved to {output_file}")
    
    # Print summary
    print("\n📊 Summary:")
    for round_data in rounds[:5]:  # Show first 5 rounds
        round_id = round_data["round_id"]
        n_tasks = round_data["n_tasks"]
        n_miners = round_data["n_miners"]
        n_winners = round_data["n_winners"]
        top_score = round_data["winners"][0]["score"] if round_data["winners"] else 0.0
        print(f"  {round_id}: {n_tasks} tasks, {n_miners} miners, {n_winners} winners, top score: {top_score}")
    
    if len(rounds) > 5:
        print(f"  ... and {len(rounds) - 5} more rounds")

if __name__ == "__main__":
    main()
