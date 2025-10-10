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
    
    def distinct(self, field: str, filter_dict: Dict[str, Any] = None) -> List[Any]:
        """Get distinct values for a field."""
        distinct_values = set()
        
        for document in self._data:
            if filter_dict is None or self._apply_filter(filter_dict, document):
                # Handle nested field access
                if "." in field:
                    keys = field.split(".")
                    value = document
                    try:
                        for k in keys:
                            value = value[k]
                        if value is not None:
                            distinct_values.add(value)
                    except (KeyError, TypeError):
                        continue
                else:
                    if field in document and document[field] is not None:
                        distinct_values.add(document[field])
        
        return list(distinct_values)
    
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
                # Enhanced group by implementation
                group_spec = stage["$group"]
                groups = {}
                
                for doc in docs:
                    # Handle complex _id grouping
                    if isinstance(group_spec["_id"], dict):
                        # Handle nested grouping like {"agent_name": "$agent_runs.miner_info.miner_hotkey", "day": {"$dayOfYear": ...}}
                        group_key = {}
                        for key, value in group_spec["_id"].items():
                            if isinstance(value, dict) and "$dayOfYear" in value:
                                # Mock day of year calculation
                                timestamp = doc.get("started_at", time.time())
                                day_of_year = int((timestamp % (365 * 24 * 60 * 60)) / (24 * 60 * 60)) + 1
                                group_key[key] = day_of_year
                            else:
                                # Handle nested field access
                                if isinstance(value, str) and value.startswith("$"):
                                    field_path = value[1:]  # Remove $
                                    if "." in field_path:
                                        keys = field_path.split(".")
                                        val = doc
                                        try:
                                            for k in keys:
                                                val = val[k]
                                            group_key[key] = val
                                        except (KeyError, TypeError):
                                            group_key[key] = None
                                    else:
                                        group_key[key] = doc.get(field_path)
                                else:
                                    group_key[key] = value
                        group_key = tuple(sorted(group_key.items()))
                    else:
                        group_key = doc.get(group_spec["_id"])
                    
                    if group_key not in groups:
                        groups[group_key] = {}
                    
                    # Apply aggregation operations
                    for field, operation in group_spec.items():
                        if field == "_id":
                            continue
                        
                        if operation == {"$sum": 1}:
                            groups[group_key][field] = groups[group_key].get(field, 0) + 1
                        elif isinstance(operation, dict) and "$sum" in operation:
                            sum_field = operation["$sum"]
                            if sum_field.startswith("$"):
                                sum_field = sum_field[1:]
                                if "." in sum_field:
                                    keys = sum_field.split(".")
                                    val = doc
                                    try:
                                        for k in keys:
                                            val = val[k]
                                        groups[group_key][field] = groups[group_key].get(field, 0) + (val or 0)
                                    except (KeyError, TypeError):
                                        pass
                                else:
                                    groups[group_key][field] = groups[group_key].get(field, 0) + (doc.get(sum_field, 0) or 0)
                            else:
                                groups[group_key][field] = groups[group_key].get(field, 0) + 1
                        elif isinstance(operation, dict) and "$avg" in operation:
                            avg_field = operation["$avg"]
                            if avg_field.startswith("$"):
                                avg_field = avg_field[1:]
                                if "." in avg_field:
                                    keys = avg_field.split(".")
                                    val = doc
                                    try:
                                        for k in keys:
                                            val = val[k]
                                        if field not in groups[group_key]:
                                            groups[group_key][field] = {"sum": 0, "count": 0}
                                        groups[group_key][field]["sum"] += (val or 0)
                                        groups[group_key][field]["count"] += 1
                                    except (KeyError, TypeError):
                                        pass
                                else:
                                    if field not in groups[group_key]:
                                        groups[group_key][field] = {"sum": 0, "count": 0}
                                    groups[group_key][field]["sum"] += (doc.get(avg_field, 0) or 0)
                                    groups[group_key][field]["count"] += 1
                        elif isinstance(operation, dict) and "$min" in operation:
                            min_field = operation["$min"]
                            if min_field.startswith("$"):
                                min_field = min_field[1:]
                                if "." in min_field:
                                    keys = min_field.split(".")
                                    val = doc
                                    try:
                                        for k in keys:
                                            val = val[k]
                                        current = groups[group_key].get(field)
                                        if current is None or (val is not None and val < current):
                                            groups[group_key][field] = val
                                    except (KeyError, TypeError):
                                        pass
                                else:
                                    current = groups[group_key].get(field)
                                    new_val = doc.get(min_field)
                                    if current is None or (new_val is not None and new_val < current):
                                        groups[group_key][field] = new_val
                        elif isinstance(operation, dict) and "$first" in operation:
                            first_field = operation["$first"]
                            if first_field.startswith("$"):
                                first_field = first_field[1:]
                                if "." in first_field:
                                    keys = first_field.split(".")
                                    val = doc
                                    try:
                                        for k in keys:
                                            val = val[k]
                                        if field not in groups[group_key]:
                                            groups[group_key][field] = val
                                    except (KeyError, TypeError):
                                        pass
                                else:
                                    if field not in groups[group_key]:
                                        groups[group_key][field] = doc.get(first_field)
                        elif isinstance(operation, dict) and "$push" in operation:
                            push_spec = operation["$push"]
                            if field not in groups[group_key]:
                                groups[group_key][field] = []
                            
                            push_doc = {}
                            for push_field, push_value in push_spec.items():
                                if isinstance(push_value, str) and push_value.startswith("$"):
                                    push_value = push_value[1:]
                                    if "." in push_value:
                                        keys = push_value.split(".")
                                        val = doc
                                        try:
                                            for k in keys:
                                                val = val[k]
                                            push_doc[push_field] = val
                                        except (KeyError, TypeError):
                                            push_doc[push_field] = None
                                    else:
                                        push_doc[push_field] = doc.get(push_value)
                                else:
                                    push_doc[push_field] = push_value
                            
                            groups[group_key][field].append(push_doc)
                
                # Convert groups back to documents
                docs = []
                for group_key, group_data in groups.items():
                    if isinstance(group_key, tuple):
                        # Handle complex grouping
                        doc = {"_id": dict(group_key)}
                    else:
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
            
            elif "$lookup" in stage:
                # Simple lookup implementation
                lookup_spec = stage["$lookup"]
                from_collection = lookup_spec["from"]
                local_field = lookup_spec["localField"]
                foreign_field = lookup_spec["foreignField"]
                as_field = lookup_spec["as"]
                
                # Get the foreign collection data
                foreign_collection = self._get_foreign_collection(from_collection)
                
                for doc in docs:
                    lookup_value = doc.get(local_field)
                    matches = []
                    
                    for foreign_doc in foreign_collection:
                        if foreign_doc.get(foreign_field) == lookup_value:
                            matches.append(foreign_doc)
                    
                    doc[as_field] = matches
            
            elif "$unwind" in stage:
                # Unwind array fields
                unwind_field = stage["$unwind"]
                if isinstance(unwind_field, str):
                    unwind_field = f"${unwind_field}"
                
                new_docs = []
                for doc in docs:
                    if unwind_field.startswith("$"):
                        field_name = unwind_field[1:]
                        array_field = doc.get(field_name, [])
                        if isinstance(array_field, list):
                            for item in array_field:
                                new_doc = doc.copy()
                                new_doc[field_name] = item
                                new_docs.append(new_doc)
                        else:
                            new_docs.append(doc)
                    else:
                        new_docs.append(doc)
                docs = new_docs
            
            elif "$addFields" in stage:
                # Add computed fields
                add_fields_spec = stage["$addFields"]
                for doc in docs:
                    for field, expression in add_fields_spec.items():
                        if isinstance(expression, dict):
                            if "$max" in expression:
                                # Handle $max operation
                                max_field = expression["$max"]
                                if isinstance(max_field, list):
                                    max_values = []
                                    for field_ref in max_field:
                                        if isinstance(field_ref, str) and field_ref.startswith("$"):
                                            field_path = field_ref[1:]
                                            if "." in field_path:
                                                keys = field_path.split(".")
                                                val = doc
                                                try:
                                                    for k in keys:
                                                        val = val[k]
                                                    if isinstance(val, list):
                                                        max_values.extend(val)
                                                except (KeyError, TypeError):
                                                    pass
                                            else:
                                                val = doc.get(field_path)
                                                if isinstance(val, list):
                                                    max_values.extend(val)
                                    doc[field] = max(max_values) if max_values else None
                            elif "$avg" in expression:
                                # Handle $avg operation
                                avg_field = expression["$avg"]
                                if isinstance(avg_field, list):
                                    avg_values = []
                                    for field_ref in avg_field:
                                        if isinstance(field_ref, str) and field_ref.startswith("$"):
                                            field_path = field_ref[1:]
                                            if "." in field_path:
                                                keys = field_path.split(".")
                                                val = doc
                                                try:
                                                    for k in keys:
                                                        val = val[k]
                                                    if isinstance(val, list):
                                                        avg_values.extend(val)
                                                except (KeyError, TypeError):
                                                    pass
                                            else:
                                                val = doc.get(field_path)
                                                if isinstance(val, list):
                                                    avg_values.extend(val)
                                    doc[field] = sum(avg_values) / len(avg_values) if avg_values else 0
                            elif "$size" in expression:
                                # Handle $size operation
                                size_field = expression["$size"]
                                if isinstance(size_field, str) and size_field.startswith("$"):
                                    field_path = size_field[1:]
                                    if "." in field_path:
                                        keys = field_path.split(".")
                                        val = doc
                                        try:
                                            for k in keys:
                                                val = val[k]
                                            doc[field] = len(val) if isinstance(val, list) else 0
                                        except (KeyError, TypeError):
                                            doc[field] = 0
                                    else:
                                        val = doc.get(field_path)
                                        doc[field] = len(val) if isinstance(val, list) else 0
                        else:
                            doc[field] = expression
        
        return MockCursor(docs)
    
    def _get_foreign_collection(self, collection_name: str) -> List[Dict[str, Any]]:
        """Get data from a foreign collection for lookup operations."""
        try:
            # This is a simple implementation that loads the foreign collection
            # In a real implementation, this would access the database
            foreign_file = self.data_dir / f"{collection_name}.json"
            if foreign_file.exists():
                with open(foreign_file, 'r') as f:
                    return json.load(f)
            return []
        except Exception:
            return []


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
        _mock_client = MockMongoClient(data_dir="data/mock")
    return _mock_client


def get_mock_db(database_name: str = "autoppia_test") -> MockDatabase:
    """Get mock database."""
    client = get_mock_client()
    return client[database_name]
