#!/usr/bin/env python3
"""
Generate realistic, varied data for the dashboard with different winners and more miners.
"""
import json
import random
import time
import uuid
from datetime import datetime, timedelta

def generate_realistic_rounds_data():
    """Generate realistic data for rounds 1-20 with varied winners and more miners."""
    
    # Generate rounds
    rounds = []
    agent_evaluation_runs = []
    tasks = []
    task_solutions = []
    evaluation_results = []
    
    # Base timestamp for round 1 (20 days ago)
    base_timestamp = time.time() - (20 * 24 * 60 * 60)  # 20 days ago
    
    # Define a pool of potential top miners with varying performance
    # SOTA agents always get top scores
    top_miners_pool = [
        {"uid": 0, "name": "OpenAI GPT-4o", "base_score": 0.95, "consistency": 0.98},
        {"uid": 1, "name": "Claude 3.5 Sonnet", "base_score": 0.93, "consistency": 0.96},
        {"uid": 2, "name": "Browser-Use", "base_score": 0.91, "consistency": 0.94},
        {"uid": 25, "name": "AutoPPIA Agent", "base_score": 0.88, "consistency": 0.95},
        {"uid": 84, "name": "Browser-Use Pro", "base_score": 0.86, "consistency": 0.90},
        {"uid": 36, "name": "Claude Web Agent", "base_score": 0.84, "consistency": 0.88},
        {"uid": 42, "name": "GPT-4 Web", "base_score": 0.82, "consistency": 0.92},
        {"uid": 67, "name": "Selenium Master", "base_score": 0.80, "consistency": 0.85},
        {"uid": 91, "name": "Playwright Pro", "base_score": 0.78, "consistency": 0.87},
        {"uid": 15, "name": "WebDriver Elite", "base_score": 0.76, "consistency": 0.89},
        {"uid": 73, "name": "Puppeteer Expert", "base_score": 0.74, "consistency": 0.86},
    ]
    
    for round_num in range(1, 21):
        # Calculate timestamps for this round
        round_start = base_timestamp + (round_num - 1) * (24 * 60 * 60)  # 1 day per round
        round_end = round_start + random.randint(1800, 3600)  # 30-60 minutes duration
        
        # Generate realistic data
        n_tasks = random.randint(8, 15)
        n_miners = random.randint(120, 180)  # More miners per round
        n_winners = random.randint(8, 15)  # More winners
        
        # Always include SOTA agents (UIDs 0, 1, 2) as top performers
        sota_agents = [miner for miner in top_miners_pool if miner["uid"] in [0, 1, 2]]
        other_agents = [miner for miner in top_miners_pool if miner["uid"] not in [0, 1, 2]]
        
        # Select additional top performers from the rest
        additional_count = random.randint(2, 5)  # 2-5 additional agents
        additional_agents = random.sample(other_agents, min(additional_count, len(other_agents)))
        
        # Combine SOTA agents with additional top performers
        round_top_miners = sota_agents + additional_agents
        
        # Generate winners with realistic variation
        winners = []
        
        # Add top performers with some randomness
        for i, miner in enumerate(round_top_miners):
            # Add variance based on consistency
            variance = random.uniform(-0.05, 0.05) * (1 - miner["consistency"])
            score = round(miner["base_score"] + variance, 3)
            score = max(0.1, min(1.0, score))  # Clamp between 0.1 and 1.0
            
            winners.append({
                "miner_uid": miner["uid"],
                "score": score,
                "rank": i + 1,
                "reward": round(score * 100, 2),
                "task_id": f"task_{random.randint(1, n_tasks)}"
            })
        
        # Add remaining winners with random scores
        for i in range(len(round_top_miners), n_winners):
            score = round(random.uniform(0.3, 0.85), 3)
            winners.append({
                "miner_uid": random.randint(0, 255),
                "score": score,
                "rank": i + 1,
                "reward": round(score * 100, 2),
                "task_id": f"task_{random.randint(1, n_tasks)}"
            })
        
        # Sort winners by score (descending)
        winners.sort(key=lambda x: x["score"], reverse=True)
        
        # Reassign ranks after sorting
        for i, winner in enumerate(winners):
            winner["rank"] = i + 1
        
        # Generate miners list with varied UIDs
        miners = []
        used_uids = set()
        
        # Add top miners for this round
        for miner in round_top_miners:
            miners.append({
                "uid": miner["uid"],
                "hotkey": f"5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY{miner['uid']:02d}",
                "coldkey": None,
                "agent_name": miner["name"],
                "agent_image": f"/agents/{miner['name'].lower().replace(' ', '_').replace('-', '_')}.png",
                "github": f"https://github.com/agents/{miner['name'].lower().replace(' ', '-')}"
            })
            used_uids.add(miner["uid"])
        
        # Add remaining random miners
        remaining_miners = n_miners - len(round_top_miners)
        for i in range(remaining_miners):
            # Generate random UID from 0-255, avoiding duplicates
            while True:
                uid = random.randint(0, 255)
                if uid not in used_uids:
                    used_uids.add(uid)
                    break
            
            miners.append({
                "uid": uid,
                "hotkey": f"5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY{uid:02d}",
                "coldkey": None,
                "agent_name": f"Agent {uid}",
                "agent_image": f"/agents/agent_{uid}.png",
                "github": f"https://github.com/agents/agent-{uid}"
            })
        
        # Calculate statistics
        all_scores = [w["score"] for w in winners]
        average_score = sum(all_scores) / len(all_scores) if all_scores else 0.0
        top_score = max(all_scores) if all_scores else 0.0
        
        # Generate validators with round-specific performance variations and unique characteristics
        validator_templates = [
            {"uid": 124, "name": "Autoppia", "base_stake": 1500.0, "base_vtrust": 0.86, "version": "7.0.0", "performance_tier": "high"},
            {"uid": 129, "name": "tao5", "base_stake": 850.0, "base_vtrust": 0.72, "version": "6.8.2", "performance_tier": "medium"},
            {"uid": 133, "name": "RoundTable21", "base_stake": 1434.0, "base_vtrust": 0.80, "version": "7.0.3", "performance_tier": "high"},
            {"uid": 135, "name": "Kraken", "base_stake": 1200.0, "base_vtrust": 0.75, "version": "6.9.1", "performance_tier": "medium"},
            {"uid": 137, "name": "Yuma", "base_stake": 1100.0, "base_vtrust": 0.78, "version": "6.7.4", "performance_tier": "medium"}
        ]
        
        validators = []
        for template in validator_templates:
            # Add round-specific variations to make each validator unique per round
            round_factor = 1.0 + (round_num - 1) * 0.02  # Slight growth over rounds
            
            # Performance variation based on tier and round
            if template["performance_tier"] == "high":
                performance_variation = random.uniform(0.9, 1.15)  # High performers
            elif template["performance_tier"] == "medium":
                performance_variation = random.uniform(0.8, 1.1)   # Medium performers
            else:  # low
                performance_variation = random.uniform(0.7, 1.0)   # Lower performers
            
            stake = template["base_stake"] * round_factor * performance_variation
            vtrust = template["base_vtrust"] * performance_variation
            vtrust = min(1.0, max(0.1, vtrust))  # Clamp between 0.1 and 1.0
            
            validators.append({
                "uid": template["uid"],
                "hotkey": f"5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY{template['uid']:02d}",
                "coldkey": None,
                "stake": round(stake, 2),
                "vtrust": round(vtrust, 3),
                "name": template["name"],
                "version": template["version"],
                "performance_tier": template["performance_tier"]  # Add performance tier for API use
            })
        
        # Create round data
        round_data = {
            "round_id": f"round_{round_num:03d}",
            "validators": validators,
            "start_block": int(round_start),
            "start_epoch": round_num,
            "end_block": int(round_end),
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
            "weights": {w["miner_uid"]: w["score"] for w in winners},
            "average_score": round(average_score, 3),
            "top_score": round(top_score, 3),
            "status": "completed" if round_num < 20 else "active"
        }
        
        rounds.append(round_data)
        
        # Generate agent evaluation runs for this round
        for i, miner in enumerate(miners[:n_winners]):  # Only for winners
            agent_run_id = f"round_{round_num:03d}_{i+1}"
            
            # Get miner's score from winners
            miner_score = 0.0
            for winner in winners:
                if winner["miner_uid"] == miner["uid"]:
                    miner_score = winner["score"]
                    break
            
            agent_run = {
                "agent_run_id": agent_run_id,
                "round_id": f"round_{round_num:03d}",
                "validator_uid": validators[0]["uid"],
                "miner_uid": miner["uid"],
                "version": "1.0",
                "started_at": round_start + random.randint(0, 300),
                "ended_at": round_end - random.randint(0, 300),
                "elapsed_sec": round_end - round_start - random.randint(0, 300),
                "avg_eval_score": miner_score,
                "avg_execution_time": random.uniform(2.5, 8.0),
                "avg_reward": round(miner_score * 100, 2),
                "rank": i + 1,
                "weight": round(miner_score * 1000, 2)
            }
            
            agent_evaluation_runs.append(agent_run)
    
    return {
        "rounds": rounds,
        "agent_evaluation_runs": agent_evaluation_runs,
        "tasks": tasks,
        "task_solutions": task_solutions,
        "evaluation_results": evaluation_results
    }

def main():
    """Generate and save realistic data."""
    print("🔄 Generating realistic, varied data...")
    
    data = generate_realistic_rounds_data()
    
    # Save to files
    with open("/home/usuario1/autoppia/autoppia_bittensor_dashboard_backend/data/mock/rounds.json", "w") as f:
        json.dump(data["rounds"], f, indent=2)
    
    with open("/home/usuario1/autoppia/autoppia_bittensor_dashboard_backend/data/mock/agent_evaluation_runs.json", "w") as f:
        json.dump(data["agent_evaluation_runs"], f, indent=2)
    
    print("✅ Realistic data generated successfully!")
    print(f"📊 Generated {len(data['rounds'])} rounds")
    print(f"👥 Generated {len(data['agent_evaluation_runs'])} agent evaluation runs")
    print("🎯 Data now includes:")
    print("   - Varied winners across rounds")
    print("   - More miners per round (120-180)")
    print("   - Realistic score variations")
    print("   - Different top performers")

if __name__ == "__main__":
    main()
