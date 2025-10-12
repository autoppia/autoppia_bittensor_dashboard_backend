# Optimized Miner Endpoints

This document describes the two optimized endpoints for efficient miner data retrieval.

## Overview

We have created two specialized endpoints to optimize data transfer and improve performance:

1. **Minimal List Endpoint** - Returns only essential fields for listing miners
2. **Detail Endpoint** - Returns complete information for a specific miner

## Endpoints

### 1. Get Minimal Miner List

**Endpoint:** `GET /api/v1/miner-list/`

**Purpose:** Get a minimal list of miners with only essential fields for efficient listing.

**Query Parameters:**
- `page` (int, optional): Page number (default: 1, min: 1)
- `limit` (int, optional): Items per page (default: 50, min: 1, max: 100)
- `isSota` (boolean, optional): Filter by SOTA status (true for company agents, false for regular miners)
- `search` (string, optional): Search by miner name or UID

**Response Fields (Minimal):**
- `uid` (int): Miner UID
- `name` (string): Miner name
- `ranking` (int): Current ranking based on average score
- `score` (float): Average score
- `isSota` (boolean): Whether miner is SOTA (company agent)
- `imageUrl` (string): Miner image URL

**Example Request:**
```bash
GET /api/v1/miner-list/?limit=10&isSota=true
```

**Example Response:**
```json
{
  "miners": [
    {
      "uid": 456,
      "name": "OpenAI CUA",
      "ranking": 1,
      "score": 0.82,
      "isSota": true,
      "imageUrl": "https://example.com/avatar.png"
    },
    {
      "uid": 789,
      "name": "Anthropic CUA",
      "ranking": 2,
      "score": 0.79,
      "isSota": true,
      "imageUrl": "https://example.com/avatar.png"
    }
  ],
  "total": 4,
  "page": 1,
  "limit": 10
}
```

### 2. Get Miner Details

**Endpoint:** `GET /api/v1/miner-list/{uid}`

**Purpose:** Get complete details for a specific miner by UID.

**Path Parameters:**
- `uid` (int): Miner UID

**Response Fields (Complete):**
- `uid` (int): Miner UID
- `name` (string): Miner name
- `hotkey` (string): Miner hotkey
- `imageUrl` (string): Miner image URL
- `githubUrl` (string, optional): GitHub repository URL
- `taostatsUrl` (string): Taostats URL
- `isSota` (boolean): Whether miner is SOTA
- `status` (string): Miner status
- `description` (string, optional): Miner description
- `totalRuns` (int): Total number of runs
- `successfulRuns` (int): Number of successful runs
- `averageScore` (float): Average score
- `bestScore` (float): Best score achieved
- `successRate` (float): Success rate percentage
- `averageDuration` (float): Average duration in seconds
- `totalTasks` (int): Total number of tasks
- `completedTasks` (int): Number of completed tasks
- `lastSeen` (string): Last seen timestamp (ISO 8601)
- `createdAt` (string): Creation timestamp (ISO 8601)
- `updatedAt` (string): Last update timestamp (ISO 8601)

**Example Request:**
```bash
GET /api/v1/miner-list/456
```

**Example Response:**
```json
{
  "miner": {
    "uid": 456,
    "name": "OpenAI CUA",
    "hotkey": "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",
    "imageUrl": "https://example.com/avatar.png",
    "githubUrl": "https://github.com/openai/computer-use-agent",
    "taostatsUrl": "https://taostats.io/miner/456",
    "isSota": true,
    "status": "active",
    "description": "OpenAI's Computer Use Agent for web automation",
    "totalRuns": 892,
    "successfulRuns": 757,
    "averageScore": 0.82,
    "bestScore": 0.91,
    "successRate": 84.8,
    "averageDuration": 35.2,
    "totalTasks": 4460,
    "completedTasks": 3780,
    "lastSeen": "2025-10-12T05:15:01.780417",
    "createdAt": "2024-01-12T14:00:00",
    "updatedAt": "2025-10-12T05:15:01.780429"
  }
}
```

## Usage Examples

### Frontend Integration

#### 1. Load Miner List (Sidebar)
```javascript
// Get all miners for sidebar
const response = await fetch('/api/v1/miner-list/?limit=50');
const data = await response.json();

// Display miners in sidebar
data.miners.forEach(miner => {
  console.log(`${miner.ranking}. ${miner.name} (UID: ${miner.uid}) - Score: ${miner.score}`);
  if (miner.isSota) {
    console.log('  🏆 SOTA Agent');
  }
});
```

#### 2. Filter SOTA Agents
```javascript
// Get only SOTA agents
const response = await fetch('/api/v1/miner-list/?isSota=true');
const data = await response.json();

// Display SOTA agents
data.miners.forEach(miner => {
  console.log(`🏆 ${miner.name} - Ranking: ${miner.ranking}, Score: ${miner.score}`);
});
```

#### 3. Search Miners
```javascript
// Search for specific miners
const searchTerm = 'openai';
const response = await fetch(`/api/v1/miner-list/?search=${searchTerm}`);
const data = await response.json();

// Display search results
data.miners.forEach(miner => {
  console.log(`Found: ${miner.name} (UID: ${miner.uid}) - Score: ${miner.score}`);
});
```

#### 4. Load Miner Details
```javascript
// Get complete miner details
const uid = 456; // OpenAI CUA
const response = await fetch(`/api/v1/miner-list/${uid}`);
const data = await response.json();

const miner = data.miner;
console.log(`Name: ${miner.name}`);
console.log(`UID: ${miner.uid}`);
console.log(`Hotkey: ${miner.hotkey}`);
console.log(`GitHub: ${miner.githubUrl}`);
console.log(`TaoStats: ${miner.taostatsUrl}`);
console.log(`Description: ${miner.description}`);
console.log(`Is SOTA: ${miner.isSota}`);
console.log(`Average Score: ${miner.averageScore}`);
console.log(`Success Rate: ${miner.successRate}%`);
```

### React Component Example

```jsx
import React, { useState, useEffect } from 'react';

function MinerList() {
  const [miners, setMiners] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showSotaOnly, setShowSotaOnly] = useState(false);

  useEffect(() => {
    const fetchMiners = async () => {
      const url = showSotaOnly 
        ? '/api/v1/miner-list/?isSota=true'
        : '/api/v1/miner-list/?limit=50';
      
      const response = await fetch(url);
      const data = await response.json();
      setMiners(data.miners);
      setLoading(false);
    };

    fetchMiners();
  }, [showSotaOnly]);

  if (loading) return <div>Loading...</div>;

  return (
    <div>
      <label>
        <input 
          type="checkbox" 
          checked={showSotaOnly}
          onChange={(e) => setShowSotaOnly(e.target.checked)}
        />
        Show SOTA agents only
      </label>
      
      <ul>
        {miners.map(miner => (
          <li key={miner.uid}>
            <img src={miner.imageUrl} alt={miner.name} />
            <span>#{miner.ranking} {miner.name} - Score: {miner.score}</span>
            {miner.isSota && <span>🏆</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}
```

## Performance Benefits

### Data Size Comparison

**Minimal List Endpoint:**
- **Fields per miner:** 6 fields
- **Data size:** ~180 bytes per miner
- **50 miners:** ~9 KB

**Full Miners Endpoint (for comparison):**
- **Fields per miner:** 21 fields  
- **Data size:** ~800 bytes per miner
- **50 miners:** ~40 KB

**Performance Improvement:** ~77% reduction in data transfer for listing

### Use Cases

1. **Sidebar/List View:** Use minimal endpoint for fast loading
2. **Detail View:** Use detail endpoint when user clicks on specific miner
3. **Search/Filter:** Use minimal endpoint with query parameters
4. **Mobile Apps:** Minimal endpoint reduces bandwidth usage

## Error Handling

### Common Error Responses

**404 Not Found:**
```json
{
  "detail": "Miner with UID '999' not found"
}
```

**500 Internal Server Error:**
```json
{
  "detail": "Failed to retrieve miner list: [error message]"
}
```

### Error Handling Example

```javascript
async function getMinerDetails(uid) {
  try {
    const response = await fetch(`/api/v1/miner-list/${uid}`);
    
    if (!response.ok) {
      if (response.status === 404) {
        throw new Error('Miner not found');
      }
      throw new Error('Failed to fetch miner details');
    }
    
    const data = await response.json();
    return data.miner;
  } catch (error) {
    console.error('Error:', error.message);
    return null;
  }
}
```

## Migration Guide

### From Existing Endpoints

**Old way (full data):**
```javascript
// Old: Get all miners with full data
const response = await fetch('/api/v1/miners?limit=50');
const data = await response.json();
// Returns 21 fields per miner
```

**New way (optimized):**
```javascript
// New: Get minimal data for listing
const response = await fetch('/api/v1/miner-list/?limit=50');
const data = await response.json();
// Returns only 5 fields per miner

// Get full data only when needed
const detailResponse = await fetch(`/api/v1/miner-list/${uid}`);
const detailData = await detailResponse.json();
// Returns all 19 fields for specific miner
```

## Summary

These optimized endpoints provide:

✅ **80% reduction** in data transfer for listing  
✅ **Faster loading** times for miner lists  
✅ **Better mobile experience** with reduced bandwidth  
✅ **Flexible filtering** with query parameters  
✅ **Complete details** when needed  
✅ **Proper ranking** based on performance  
✅ **SOTA classification** for company agents  

Use the minimal endpoint for lists and the detail endpoint for individual miner information.
