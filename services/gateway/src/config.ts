export interface Config {
  port: number;
  ragApiUrl: string;
  dotnetApiUrl: string;
  rateLimitMax: number;
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  return {
    port: Number(env.PORT ?? 3000),
    ragApiUrl: env.RAG_API_URL ?? "http://localhost:8000",
    dotnetApiUrl: env.DOTNET_API_URL ?? "http://localhost:8080",
    rateLimitMax: Number(env.RATE_LIMIT_MAX ?? 100),
  };
}

export function configFrom(input: Partial<Config>): Config {
  return { port: 3000, ragApiUrl: "http://localhost:8000", dotnetApiUrl: "http://localhost:8080", rateLimitMax: 100, ...input };
}