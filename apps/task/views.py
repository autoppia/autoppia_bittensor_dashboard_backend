from django.conf import settings
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from pymongo import MongoClient
from datetime import datetime, timezone, timedelta


class TaskViewSet(viewsets.ViewSet):
    mongo_connection_uri = settings.MONGO_CONNECTION_URI
    mongo_client = MongoClient(mongo_connection_uri)
    mongo_database = mongo_client["autoppia"]

    def create(self, request):
        validator_uid = request.data.get("validator_uid")
        miner_uid = request.data.get("miner_uid")
        miner_hotkey = request.data.get("miner_hotkey")
        task_id = request.data.get("task_id")
        success = request.data.get("success")
        score = request.data.get("score")
        duration = request.data.get("duration")
        website = request.data.get("website")
        created_at = request.data.get("created_at", datetime.now(timezone.utc).timestamp())

        task_data = {
            "validator_uid": validator_uid,
            "miner_uid": miner_uid,
            "miner_hotkey": miner_hotkey,
            "task_id": task_id,
            "success": success,
            "score": score,
            "duration": duration,
            "website": website,
            "created_at": created_at
        }

        result = self.mongo_database["tasks"].insert_one(task_data)
        if not result.acknowledged:
            return Response({"error": "Failed to create task log"}, status=500)

        metric = self.mongo_database["metrics"].find_one({"miner_uid": miner_uid})
        validator_uid_string = str(validator_uid)

        if metric and metric["miner_hotkey"] == miner_hotkey:
            if metric["tasks_per_validator"].get(validator_uid_string):
                total_score = metric["scores"][validator_uid_string] * metric["tasks_per_validator"][validator_uid_string] + score                
                total_duration = metric["durations"][validator_uid_string] * metric["tasks_per_validator"][validator_uid_string] + duration
                metric["tasks_per_validator"][validator_uid_string] += 1

                metric["scores"][validator_uid_string] = round(total_score / metric["tasks_per_validator"][validator_uid_string], 3)            
                metric["durations"][validator_uid_string] = round(total_duration / metric["tasks_per_validator"][validator_uid_string])
                
            else:             
                metric["tasks_per_validator"][validator_uid_string] = 1

                metric["scores"][validator_uid_string] = score

                metric["durations"][validator_uid_string] = duration        
                metric["duration_avg"] = duration      

            metric["score_avg"] = sum(metric["scores"].values()) / len(metric["scores"].values())      
            metric["score_avg"] = round(metric["score_avg"], 3) 

            metric["duration_avg"] = sum(metric["durations"].values()) / len(metric["durations"].values())
            metric["duration_avg"] = round(metric["duration_avg"])

            metric["successful_tasks"] += 1 if success else 0
            metric["total_tasks"] += 1
            metric["success_rate"] = round(metric["successful_tasks"] / metric["total_tasks"], 3)

            result = self.mongo_database["metrics"].replace_one({"miner_uid": miner_uid}, metric)
            if not result.acknowledged:
                return Response({"error": "Failed to update metric"}, status=500)

        else:
            new_metric = {
                "miner_uid": miner_uid,
                "miner_hotkey": miner_hotkey,
                "tasks_per_validator": {
                    validator_uid_string: 1
                },
                "scores": {
                    validator_uid_string: score
                },
                "durations": {
                    validator_uid_string: duration
                },
                "successful_tasks": 1 if success else 0,
                "total_tasks": 1,
                "success_rate": 1 if success else 0,
            }

            if metric:                
                result = self.mongo_database["metrics"].replace_one({"miner_uid": miner_uid}, new_metric)
            else:
                result = self.mongo_database["metrics"].insert_one(new_metric)

            if not result.acknowledged:
                return Response({"error": "Failed to log task"}, status=500)

        if result.acknowledged:
            return Response({"message": "Task logged successfully"}, status=201)
        else:
            return Response({"message": "Failed to log task"}, status=500)
        
    @action(detail=False, url_path="filtered")
    def filtered_tasks(self, request):
        period = request.GET.get("period", "All")
        websites = request.GET.get("websites", "")
        websites = websites.split(",")

        query = {}

        now = datetime.now(timezone.utc)
        if period == "Day":
            start_date = now - timedelta(days=1)
        elif period == "Week":
            start_date = now - timedelta(days=7)
        elif period == "Month":
            start_date = now - timedelta(days=30)

        if period != "All":
            query["created_at"] = {"$gte": start_date.timestamp()}

        if websites:
            query["website"] = {"$in": websites}

        pipeline = [
            {
                "$match": query
            },
            {
                "$group": {
                    "_id": {
                        "miner_uid": "$miner_uid",
                        "miner_hotkey": "$miner_hotkey",
                        "validator_uid": "$validator_uid"
                    }, 
                    "score": {"$avg": "$score"},
                    "duration": {"$avg": "$duration"}
                }
            },
            {
                "$group": {
                    "_id": {
                        "miner_uid": "$_id.miner_uid",
                        "miner_hotkey": "$_id.miner_hotkey"
                    },
                    "scores": {
                        "$push": {
                            "k": {"$toString": "$_id.validator_uid"}, 
                            "v": "$score"
                        }
                    },
                    "durations": {
                        "$push": {
                            "k": {"$toString": "$_id.validator_uid"}, 
                            "v": "$duration"
                        }
                    },
                    "score_avg": {"$avg": "$score"},
                    "duration_avg": {"$avg": "$duration"}
                }
            },
            {
                "$addFields": {
                    "scores": {
                        "$arrayToObject": "$scores"
                    },
                    "durations": {
                        "$arrayToObject": "$durations"
                    }
                }
            },
            {
                "$project": {
                    "miner_uid": "$_id.miner_uid",
                    "miner_hotkey": "$_id.miner_hotkey",
                    "scores": 1,
                    "durations": 1,
                    "score_avg": { "$round": ["$score_avg", 3] },
                    "duration_avg": { "$round": ["$duration_avg", 0] },
                    "_id": 0
                }
            },
            {
                "$sort": {"miner_uid": 1}
            }
        ]
        
        tasks = self.mongo_database["tasks"].aggregate(pipeline)
        return Response(list(tasks))
        