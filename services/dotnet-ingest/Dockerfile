FROM mcr.microsoft.com/dotnet/sdk:8.0@sha256:1b955f179b02df4f0eb5fadedaa898cbe44d620a64aab52eec72c8e91d1355b5 AS build
WORKDIR /src

COPY src/IngestApi/IngestApi.csproj src/IngestApi/
RUN dotnet restore src/IngestApi/IngestApi.csproj

COPY src/IngestApi/ src/IngestApi/
RUN dotnet publish src/IngestApi/IngestApi.csproj -c Release -o /app/publish

FROM mcr.microsoft.com/dotnet/aspnet:8.0@sha256:dccacae9e0e103b5d3ba5fbe442dd8d3316b990cd8f2ec9be0a0ec9db0c0e520 AS final
WORKDIR /app
COPY --from=build /app/publish .
EXPOSE 8080
ENV ASPNETCORE_URLS=http://+:8080
ENTRYPOINT ["dotnet", "IngestApi.dll"]