import axios from "axios"

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000"

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
})

export class ApiError extends Error {
  status?: number

  constructor(message: string, status?: number) {
    super(message)
    this.name = "ApiError"
    this.status = status
  }
}

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    const message =
      error.response?.data?.detail ?? error.message ?? "Something went wrong contacting the server."
    return Promise.reject(
      new ApiError(typeof message === "string" ? message : JSON.stringify(message), error.response?.status)
    )
  }
)
