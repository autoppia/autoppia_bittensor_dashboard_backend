"""
Mock MongoDB implementation using JSON files for local testing.
This allows testing the API without requiring a real MongoDB instance.
"""

import json
import os
import asyncio
from typing import Any, Dict, List, Optional, Union
from datetime import datetime
import uuid
from pathlib import Path


class MockCollection:
    """Mock MongoDB collection using JSON files."""
    
    def __init__(self, collection_name: str, data_dir: str = "mock_data"):
        self.collection_name = collection_name
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        self.file_path = self.data_dir / f"{collection_name}.json"
        self._data = self._load_data()
    
    def _load_data(self) -> List[Dict[str, Any]]:
        """Load data from JSON file."""
        if self.file_path.exists():
            try:
                with open(self.file_path, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                return []
        return []
    
    def _save_data(self):
        """Save data to JSON file."""
        with open(self.file_path, 'w') as f:
            json.dump(self._data, f, indent=2, default=str)
    
    def _generate_id(self) -> str:
        """Generate a unique ID."""
        return str(uuid.uuid4())
    
    def _apply_filter(self, filter_dict: Dict[str, Any], document: Dict[str, Any]) -> bool:
        """Apply MongoDB-style filter to a document."""
        for key, value in filter_dict.items():
            if key == "_id":
                if document.get("_id") != value:
                    return False
            elif key.startswith("$"):
                # Handle operators like $in, $gte, etc.
                if key == "$in":
                    if document.get(list(value.keys())[0]) not in list(value.values())[0]:
                        return False
                elif key == "$gte":
                    if document.get(list(value.keys())[0]) < list(value.values())[0]:
                        return False
                elif key == "$lte":
                    if document.get(list(value.keys())[0]) > list(value.values())[0]:
                        return False
            else:
                # Handle nested field queries like "validator_info.validator_uid"
                if "." in key:
                    keys = key.split(".")
                    doc_value = document
                    try:
                        for k in keys:
                            doc_value = doc_value[k]
                        if doc_value != value:
                            return False
                    except (KeyError, TypeError):
                        return False
                else:
                    if document.get(key) != value:
                        return False
        return True
    
    async def find_one(self, filter_dict: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Find one document matching the filter."""
        for document in self._data:
            if self._apply_filter(filter_dict, document):
                return document.copy()
        return None
    
    def find(self, filter_dict: Dict[str, Any] = None) -> 'MockCursor':
        """Find documents matching the filter."""
        if filter_dict is None:
            filter_dict = {}
        
        matching_docs = []
        for document in self._data:
            if self._apply_filter(filter_dict, document):
                matching_docs.append(document.copy())
        
        return MockCursor(matching_docs)
    
    async def insert_one(self, document: Dict[str, Any]) -> 'MockInsertResult':
        """Insert one document."""
        if "_id" not in document:
            document["_id"] = self._generate_id()
        
        self._data.append(document)
        self._save_data()
        return MockInsertResult(document["_id"])
    
    async def update_one(self, filter_dict: Dict[str, Any], update_dict: Dict[str, Any], upsert: bool = False) -> 'MockUpdateResult':
        """Update one document."""
        for i, document in enumerate(self._data):
            if self._apply_filter(filter_dict, document):
                # Apply update operations
                if "$set" in update_dict:
                    document.update(update_dict["$set"])
                elif "$setOnInsert" in update_dict:
                    # Only set if this is a new document (upsert)
                    if upsert:
                        document.update(update_dict["$setOnInsert"])
                else:
                    document.update(update_dict)
                
                self._save_data()
                return MockUpdateResult(matched_count=1, modified_count=1)
        
        # If no document found and upsert is True
        if upsert:
            new_doc = filter_dict.copy()
            if "$set" in update_dict:
                new_doc.update(update_dict["$set"])
            elif "$setOnInsert" in update_dict:
                new_doc.update(update_dict["$setOnInsert"])
            else:
                new_doc.update(update_dict)
            
            new_doc["_id"] = self._generate_id()
            self._data.append(new_doc)
            self._save_data()
            return MockUpdateResult(matched_count=0, modified_count=0, upserted_id=new_doc["_id"])
        
        return MockUpdateResult(matched_count=0, modified_count=0)
    
    async def count_documents(self, filter_dict: Dict[str, Any] = None) -> int:
        """Count documents matching the filter."""
        if filter_dict is None:
            return len(self._data)
        
        count = 0
        for document in self._data:
            if self._apply_filter(filter_dict, document):
                count += 1
        return count
    
    def aggregate(self, pipeline: List[Dict[str, Any]]) -> 'MockCursor':
        """Simple aggregation pipeline support."""
        # Start with all documents
        docs = self._data.copy()
        
        for stage in pipeline:
            if "$match" in stage:
                # Apply match filter
                filter_dict = stage["$match"]
                docs = [doc for doc in docs if self._apply_filter(filter_dict, doc)]
            
            elif "$group" in stage:
                # Simple group by implementation
                group_spec = stage["$group"]
                groups = {}
                
                for doc in docs:
                    group_key = doc.get(group_spec["_id"])
                    if group_key not in groups:
                        groups[group_key] = {}
                    
                    # Apply aggregation operations
                    for field, operation in group_spec.items():
                        if field == "_id":
                            continue
                        
                        if operation == {"$sum": 1}:
                            groups[group_key][field] = groups[group_key].get(field, 0) + 1
                        elif operation == {"$sum": "$total_reward"}:
                            groups[group_key][field] = groups[group_key].get(field, 0) + doc.get("total_reward", 0)
                        elif operation == {"$sum": "$n_tasks_total"}:
                            groups[group_key][field] = groups[group_key].get(field, 0) + doc.get("n_tasks_total", 0)
                        elif operation == {"$sum": "$n_tasks_completed"}:
                            groups[group_key][field] = groups[group_key].get(field, 0) + doc.get("n_tasks_completed", 0)
                        elif operation == {"$avg": "$avg_eval_score"}:
                            # Simple average calculation
                            if field not in groups[group_key]:
                                groups[group_key][field] = {"sum": 0, "count": 0}
                            groups[group_key][field]["sum"] += doc.get("avg_eval_score", 0)
                            groups[group_key][field]["count"] += 1
                        elif operation == {"$min": "$rank"}:
                            current = groups[group_key].get(field)
                            new_val = doc.get("rank")
                            if current is None or (new_val is not None and new_val < current):
                                groups[group_key][field] = new_val
                        elif operation == {"$first": "$miner_info"}:
                            if field not in groups[group_key]:
                                groups[group_key][field] = doc.get("miner_info")
                        elif operation == {"$push": {"round_id": "$round_id", "rank": "$rank", "score": "$avg_eval_score", "reward": "$total_reward"}}:
                            if field not in groups[group_key]:
                                groups[group_key][field] = []
                            groups[group_key][field].append({
                                "round_id": doc.get("round_id"),
                                "rank": doc.get("rank"),
                                "score": doc.get("avg_eval_score"),
                                "reward": doc.get("total_reward")
                            })
                
                # Convert groups back to documents
                docs = []
                for group_key, group_data in groups.items():
                    doc = {"_id": group_key}
                    doc.update(group_data)
                    
                    # Calculate averages
                    for field, value in group_data.items():
                        if isinstance(value, dict) and "sum" in value and "count" in value:
                            doc[field] = value["sum"] / value["count"] if value["count"] > 0 else 0
                    
                    docs.append(doc)
            
            elif "$sort" in stage:
                # Simple sort implementation
                sort_spec = stage["$sort"]
                for field, direction in sort_spec.items():
                    docs.sort(key=lambda x: x.get(field, 0), reverse=(direction == -1))
            
            elif "$skip" in stage:
                docs = docs[stage["$skip"]:]
            
            elif "$limit" in stage:
                docs = docs[:stage["$limit"]]
        
        return MockCursor(docs)


class MockCursor:
    """Mock MongoDB cursor."""
    
    def __init__(self, documents: List[Dict[str, Any]]):
        self.documents = documents
        self.index = 0
    
    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        """Convert cursor to list."""
        if length is None:
            return self.documents
        return self.documents[:length]
    
    def sort(self, field: str, direction: int) -> 'MockCursor':
        """Sort the cursor."""
        sorted_docs = sorted(self.documents, key=lambda x: x.get(field, 0), reverse=(direction == -1))
        return MockCursor(sorted_docs)
    
    def skip(self, count: int) -> 'MockCursor':
        """Skip documents."""
        return MockCursor(self.documents[count:])
    
    def limit(self, count: int) -> 'MockCursor':
        """Limit documents."""
        return MockCursor(self.documents[:count])


class MockInsertResult:
    """Mock insert result."""
    
    def __init__(self, inserted_id: str):
        self.inserted_id = inserted_id


class MockUpdateResult:
    """Mock update result."""
    
    def __init__(self, matched_count: int, modified_count: int, upserted_id: Optional[str] = None):
        self.matched_count = matched_count
        self.modified_count = modified_count
        self.upserted_id = upserted_id


class MockDatabase:
    """Mock MongoDB database."""
    
    def __init__(self, name: str, data_dir: str = "mock_data"):
        self.name = name
        self.data_dir = data_dir
        self._collections = {}
    
    def __getitem__(self, collection_name: str) -> MockCollection:
        """Get collection by name."""
        if collection_name not in self._collections:
            self._collections[collection_name] = MockCollection(collection_name, self.data_dir)
        return self._collections[collection_name]
    
    def __getattr__(self, collection_name: str) -> MockCollection:
        """Get collection by attribute access."""
        return self[collection_name]


class MockMongoClient:
    """Mock MongoDB client."""
    
    def __init__(self, connection_string: str = "mock://localhost", data_dir: str = "mock_data"):
        self.connection_string = connection_string
        self.data_dir = data_dir
        self._databases = {}
    
    def __getitem__(self, database_name: str) -> MockDatabase:
        """Get database by name."""
        if database_name not in self._databases:
            self._databases[database_name] = MockDatabase(database_name, self.data_dir)
        return self._databases[database_name]
    
    async def close(self):
        """Close the mock client."""
        pass


# Global mock client instance
_mock_client: Optional[MockMongoClient] = None


def get_mock_client() -> MockMongoClient:
    """Get the global mock client instance."""
    global _mock_client
    if _mock_client is None:
        _mock_client = MockMongoClient(data_dir="mock_data")
    return _mock_client


def get_mock_db(database_name: str = "autoppia_test") -> MockDatabase:
    """Get mock database."""
    client = get_mock_client()
    return client[database_name]
