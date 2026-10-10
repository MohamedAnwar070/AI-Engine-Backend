# Advanced Node Examples - Real API Integration

## Example 1: Weather API Node

```javascript
async function executeNode(inputs) {
    const axios = require('axios');
    
    try {
        const response = await axios.get(
            `https://api.openweathermap.org/data/2.5/weather`,
            {
                params: {
                    q: inputs.city,
                    appid: 'YOUR_API_KEY',
                    units: 'metric'
                }
            }
        );
        
        return {
            success: true,
            weather: {
                city: response.data.name,
                temperature: response.data.main.temp,
                description: response.data.weather[0].description,
                humidity: response.data.main.humidity,
                wind_speed: response.data.wind.speed
            }
        };
    } catch (error) {
        return {
            success: false,
            error: error.message
        };
    }
}
```

**Schema:**
```json
{
  "inputs": {
    "city": "string"
  },
  "outputs": {
    "weather": "object"
  }
}
```

---

## Example 2: Database Query Node (MongoDB)

```javascript
async function executeNode(inputs) {
    const { MongoClient } = require('mongodb');
    
    const client = new MongoClient('mongodb://localhost:27017');
    
    try {
        await client.connect();
        const db = client.db('mydb');
        const collection = db.collection('users');
        
        const user = await collection.findOne({ 
            email: inputs.email 
        });
        
        await client.close();
        
        if (!user) {
            return {
                success: false,
                error: 'User not found'
            };
        }
        
        return {
            success: true,
            user: {
                id: user._id,
                name: user.name,
                email: user.email,
                created_at: user.created_at
            }
        };
    } catch (error) {
        await client.close();
        return {
            success: false,
            error: error.message
        };
    }
}
```

**Schema:**
```json
{
  "inputs": {
    "email": "string"
  },
  "outputs": {
    "user": "object"
  }
}
```

---

## Example 3: REST API with Authentication

```javascript
async function executeNode(inputs) {
    const axios = require('axios');
    
    try {
        // First, get auth token
        const authResponse = await axios.post(
            'https://api.example.com/auth/login',
            {
                username: 'api_user',
                password: 'api_password'
            }
        );
        
        const token = authResponse.data.token;
        
        // Make authenticated request
        const response = await axios.get(
            `https://api.example.com/jobs/search`,
            {
                headers: {
                    'Authorization': `Bearer ${token}`
                },
                params: {
                    location: inputs.location,
                    role: inputs.role,
                    experience: inputs.experience
                }
            }
        );
        
        return {
            success: true,
            jobs: response.data.jobs,
            total_count: response.data.total
        };
    } catch (error) {
        return {
            success: false,
            error: error.response?.data?.message || error.message
        };
    }
}
```

**Schema:**
```json
{
  "inputs": {
    "location": "string",
    "role": "string",
    "experience": "string"
  },
  "outputs": {
    "jobs": "array",
    "total_count": "number"
  }
}
```

---

## Example 4: GraphQL API Node

```javascript
async function executeNode(inputs) {
    const axios = require('axios');
    
    const query = `
        query GetUser($id: ID!) {
            user(id: $id) {
                id
                name
                email
                posts {
                    id
                    title
                    content
                }
            }
        }
    `;
    
    try {
        const response = await axios.post(
            'https://api.example.com/graphql',
            {
                query: query,
                variables: {
                    id: inputs.user_id
                }
            },
            {
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer YOUR_TOKEN'
                }
            }
        );
        
        if (response.data.errors) {
            return {
                success: false,
                error: response.data.errors[0].message
            };
        }
        
        return {
            success: true,
            user: response.data.data.user
        };
    } catch (error) {
        return {
            success: false,
            error: error.message
        };
    }
}
```

**Schema:**
```json
{
  "inputs": {
    "user_id": "string"
  },
  "outputs": {
    "user": "object"
  }
}
```

---

## Example 5: File Processing Node

```javascript
async function executeNode(inputs) {
    const fs = require('fs').promises;
    const path = require('path');
    
    try {
        // Read CSV file
        const filePath = path.join('/data', inputs.filename);
        const fileContent = await fs.readFile(filePath, 'utf-8');
        
        // Parse CSV
        const lines = fileContent.split('\n');
        const headers = lines[0].split(',');
        
        const data = lines.slice(1).map(line => {
            const values = line.split(',');
            const obj = {};
            headers.forEach((header, index) => {
                obj[header.trim()] = values[index]?.trim();
            });
            return obj;
        });
        
        // Filter based on inputs
        const filtered = data.filter(row => {
            if (inputs.filter_column && inputs.filter_value) {
                return row[inputs.filter_column] === inputs.filter_value;
            }
            return true;
        });
        
        return {
            success: true,
            data: filtered,
            total_rows: filtered.length
        };
    } catch (error) {
        return {
            success: false,
            error: error.message
        };
    }
}
```

**Schema:**
```json
{
  "inputs": {
    "filename": "string",
    "filter_column": "string",
    "filter_value": "string"
  },
  "outputs": {
    "data": "array",
    "total_rows": "number"
  }
}
```

---

## Example 6: Web Scraping Node

```javascript
async function executeNode(inputs) {
    const axios = require('axios');
    const cheerio = require('cheerio'); // Note: Add to package.json
    
    try {
        const response = await axios.get(inputs.url);
        const $ = cheerio.load(response.data);
        
        const results = [];
        
        // Example: Scrape job listings
        $('.job-listing').each((index, element) => {
            results.push({
                title: $(element).find('.job-title').text().trim(),
                company: $(element).find('.company-name').text().trim(),
                location: $(element).find('.location').text().trim(),
                salary: $(element).find('.salary').text().trim(),
                link: $(element).find('a').attr('href')
            });
        });
        
        return {
            success: true,
            jobs: results,
            total_found: results.length,
            source_url: inputs.url
        };
    } catch (error) {
        return {
            success: false,
            error: error.message
        };
    }
}
```

**Schema:**
```json
{
  "inputs": {
    "url": "string"
  },
  "outputs": {
    "jobs": "array",
    "total_found": "number",
    "source_url": "string"
  }
}
```

---

## Example 7: Email Sending Node

```javascript
async function executeNode(inputs) {
    const nodemailer = require('nodemailer'); // Note: Add to package.json
    
    // Create transporter
    const transporter = nodemailer.createTransport({
        host: 'smtp.gmail.com',
        port: 587,
        secure: false,
        auth: {
            user: 'your-email@gmail.com',
            pass: 'your-app-password'
        }
    });
    
    try {
        // Send email
        const info = await transporter.sendMail({
            from: '"Job Portal" <noreply@jobportal.com>',
            to: inputs.recipient_email,
            subject: inputs.subject,
            text: inputs.message,
            html: `<p>${inputs.message}</p>`
        });
        
        return {
            success: true,
            message_id: info.messageId,
            sent_to: inputs.recipient_email
        };
    } catch (error) {
        return {
            success: false,
            error: error.message
        };
    }
}
```

**Schema:**
```json
{
  "inputs": {
    "recipient_email": "string",
    "subject": "string",
    "message": "string"
  },
  "outputs": {
    "message_id": "string",
    "sent_to": "string"
  }
}
```

---

## Example 8: Payment Processing Node (Stripe)

```javascript
async function executeNode(inputs) {
    const stripe = require('stripe')('your_stripe_secret_key'); // Note: Add to package.json
    
    try {
        // Create payment intent
        const paymentIntent = await stripe.paymentIntents.create({
            amount: inputs.amount * 100, // Convert to cents
            currency: 'usd',
            payment_method_types: ['card'],
            metadata: {
                job_application_id: inputs.application_id,
                user_email: inputs.user_email
            }
        });
        
        return {
            success: true,
            client_secret: paymentIntent.client_secret,
            payment_intent_id: paymentIntent.id,
            amount: inputs.amount,
            currency: 'usd'
        };
    } catch (error) {
        return {
            success: false,
            error: error.message
        };
    }
}
```

**Schema:**
```json
{
  "inputs": {
    "amount": "number",
    "application_id": "string",
    "user_email": "string"
  },
  "outputs": {
    "client_secret": "string",
    "payment_intent_id": "string",
    "amount": "number",
    "currency": "string"
  }
}
```

---

## Example 9: SMS Notification Node (Twilio)

```javascript
async function executeNode(inputs) {
    const twilio = require('twilio'); // Note: Add to package.json
    
    const accountSid = 'YOUR_TWILIO_ACCOUNT_SID';
    const authToken = 'YOUR_TWILIO_AUTH_TOKEN';
    const client = twilio(accountSid, authToken);
    
    try {
        const message = await client.messages.create({
            body: inputs.message,
            from: '+1234567890', // Your Twilio number
            to: inputs.phone_number
        });
        
        return {
            success: true,
            message_sid: message.sid,
            status: message.status,
            sent_to: inputs.phone_number
        };
    } catch (error) {
        return {
            success: false,
            error: error.message
        };
    }
}
```

**Schema:**
```json
{
  "inputs": {
    "phone_number": "string",
    "message": "string"
  },
  "outputs": {
    "message_sid": "string",
    "status": "string",
    "sent_to": "string"
  }
}
```

---

## Example 10: AI/ML Inference Node

```javascript
async function executeNode(inputs) {
    const axios = require('axios');
    
    try {
        // Call your ML model API
        const response = await axios.post(
            'http://localhost:5000/predict', // Your ML service
            {
                text: inputs.resume_text
            },
            {
                headers: {
                    'Content-Type': 'application/json'
                }
            }
        );
        
        return {
            success: true,
            skills_detected: response.data.skills,
            experience_years: response.data.experience,
            job_categories: response.data.categories,
            confidence_score: response.data.confidence
        };
    } catch (error) {
        return {
            success: false,
            error: error.message
        };
    }
}
```

**Schema:**
```json
{
  "inputs": {
    "resume_text": "string"
  },
  "outputs": {
    "skills_detected": "array",
    "experience_years": "number",
    "job_categories": "array",
    "confidence_score": "number"
  }
}
```

---

## Tips for Creating Nodes

### 1. Error Handling
Always wrap your code in try-catch blocks and return consistent error objects:
```javascript
try {
    // Your code
} catch (error) {
    return {
        success: false,
        error: error.message,
        error_type: error.name
    };
}
```

### 2. Input Validation
```javascript
async function executeNode(inputs) {
    // Validate required inputs
    if (!inputs.email || !inputs.email.includes('@')) {
        return {
            success: false,
            error: 'Valid email is required'
        };
    }
    
    // Your code
}
```

### 3. Timeout Handling
```javascript
const axios = require('axios');

async function executeNode(inputs) {
    try {
        const response = await axios.get(inputs.url, {
            timeout: 10000 // 10 seconds
        });
        return { success: true, data: response.data };
    } catch (error) {
        if (error.code === 'ECONNABORTED') {
            return { success: false, error: 'Request timeout' };
        }
        return { success: false, error: error.message };
    }
}
```

### 4. Rate Limiting
```javascript
// Simple rate limiter
const lastCall = {};

async function executeNode(inputs) {
    const now = Date.now();
    const key = inputs.api_key || 'default';
    
    if (lastCall[key] && now - lastCall[key] < 1000) {
        return {
            success: false,
            error: 'Rate limit exceeded. Please wait.'
        };
    }
    
    lastCall[key] = now;
    
    // Your API call
}
```

### 5. Caching Results
```javascript
const cache = {};

async function executeNode(inputs) {
    const cacheKey = JSON.stringify(inputs);
    
    // Check cache
    if (cache[cacheKey] && cache[cacheKey].expires > Date.now()) {
        return {
            success: true,
            ...cache[cacheKey].data,
            from_cache: true
        };
    }
    
    // Make API call
    const result = await makeAPICall(inputs);
    
    // Cache result for 5 minutes
    cache[cacheKey] = {
        data: result,
        expires: Date.now() + 5 * 60 * 1000
    };
    
    return {
        success: true,
        ...result,
        from_cache: false
    };
}
```

---

## Installing Additional Node.js Packages

Update `package.json` with the packages you need:

```json
{
  "dependencies": {
    "axios": "^1.6.2",
    "node-fetch": "^3.3.2",
    "cheerio": "^1.0.0-rc.12",
    "nodemailer": "^6.9.7",
    "twilio": "^4.19.0",
    "stripe": "^14.9.0",
    "mongodb": "^6.3.0"
  }
}
```

Then run:
```bash
npm install
```

---

## Best Practices

1. **Keep nodes focused**: Each node should do one thing well
2. **Use descriptive names**: Help the LLM understand when to call each node
3. **Document your schema**: Clear input/output definitions
4. **Handle all errors**: Never let nodes crash without returning an error
5. **Test independently**: Test each node's code before integrating
6. **Use environment variables**: Store API keys securely
7. **Log important events**: Help with debugging
8. **Version your nodes**: Keep track of changes to node code

---

## Security Notes

⚠️ **Never hardcode sensitive data in node code!**

Instead, use environment variables or a secrets manager:

```javascript
async function executeNode(inputs) {
    const API_KEY = process.env.EXTERNAL_API_KEY;
    
    if (!API_KEY) {
        return {
            success: false,
            error: 'API key not configured'
        };
    }
    
    // Use API_KEY in your requests
}
```