using AiPlatform.IngestApi.Services;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddSingleton<IEmbeddingClient, EmbeddingClient>();
builder.Services.AddSingleton<IVectorStore, PgVectorStore>();
builder.Services.AddHttpClient();
builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen();

builder.Services.AddCors(o =>
    o.AddDefaultPolicy(p =>
        p.AllowAnyOrigin().AllowAnyHeader().AllowAnyMethod()));

var app = builder.Build();

app.UseCors();

if (app.Environment.IsDevelopment())
{
    app.UseSwagger();
    app.UseSwaggerUI();
}

var vectorStore = app.Services.GetRequiredService<IVectorStore>();

app.MapGet("/health", async (CancellationToken ct) =>
{
    var ok = await vectorStore.CanConnectAsync(ct);
    return Results.Ok(new { status = ok ? "ok" : "degraded", mode = "dotnet-ingest" });
});

app.MapPost("/ingest", async (DocumentIngestRequest req, IEmbeddingClient emb, CancellationToken ct) =>
{
    if (string.IsNullOrWhiteSpace(req.Content))
        return Results.BadRequest(new { error = "content is required" });

    var vector = await emb.EmbedAsync(req.Content, ct);
    var id = req.Id ?? Guid.NewGuid().ToString("N");
    await vectorStore.UpsertAsync(id, req.Content, vector, ct);

    return Results.Ok(new { id, dimensions = vector.Length });
});

app.MapGet("/vectors/{id}", async (string id, IVectorStore store, CancellationToken ct) =>
{
    var doc = await store.GetAsync(id, ct);
    return doc is null ? Results.NotFound() : Results.Ok(doc);
});

app.MapGet("/", () => Results.Redirect("/swagger"));

app.Run();

public record DocumentIngestRequest(string? Id, string Content);