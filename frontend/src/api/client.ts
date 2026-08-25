import axios from "axios"

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000"

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
})

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    const message =
      error.response?.data?.detail ?? error.message ?? "Something went wrong contacting the server."
    return Promise.reject(new Error(typeof message === "string" ? message : JSON.stringify(message)))
  }
)
