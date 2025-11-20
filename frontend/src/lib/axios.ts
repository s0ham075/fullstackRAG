// src/lib/axios.js
import axios from 'axios';
console.log('\n\n\n API Endpoint:', process.env.api_endpoint);
export const api = axios.create({
    baseURL: process.env.NEXT_PUBLIC_API_ENDPOINT,
});