namespace AiPlatform.IngestApi.Services;

public interface IVectorStore
{
    Task<bool> CanConnectAsync(CancellationToken ct = default);
    Task UpsertAsync(string id, string content, float[] vector, CancellationToken ct = default);
    Task<DocumentRecord?> GetAsync(string id, CancellationToken ct = default);
}

public sealed record DocumentRecord(string Id, string Content, int Dimensions, DateTime CreatedAt);

public sealed class PgVectorStore(IConfiguration config) : IVectorStore
{
    private readonly string _connectionString =
        config.GetConnectionString("Default") ?? "Host=localhost;Port=5432;Database=aiplatform;Username=ai;Password=ai";

    public async Task<bool> CanConnectAsync(CancellationToken ct = default)
    {
        try
        {
            await using var conn = new Npgsql.NpgsqlConnection(_connectionString);
            await conn.OpenAsync(ct);
            await using var cmd = conn.CreateCommand();
            cmd.CommandText = "SELECT 1";
            await cmd.ExecuteScalarAsync(ct);
            return true;
        }
        catch (Npgsql.NpgsqlException)
        {
            return false;
        }
    }

    public async Task UpsertAsync(string id, string content, float[] vector, CancellationToken ct = default)
    {
        await using var conn = new Npgsql.NpgsqlConnection(_connectionString);
        await conn.OpenAsync(ct);

        await using (var ensure = conn.CreateCommand())
        {
            ensure.CommandText = "CREATE EXTENSION IF NOT EXISTS vector";
            await ensure.ExecuteNonQueryAsync(ct);
        }

        await using (var ensure = conn.CreateCommand())
        {
            ensure.CommandText = """
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    embedding vector(384)
                );
                """;
            await ensure.ExecuteNonQueryAsync(ct);
        }

        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            INSERT INTO documents (id, content, embedding)
            VALUES (@id, @content, @embedding::vector)
            ON CONFLICT (id) DO UPDATE
            SET content = EXCLUDED.content, embedding = EXCLUDED.embedding
            """;
        cmd.Parameters.AddWithValue("id", id);
        cmd.Parameters.AddWithValue("content", content);
        cmd.Parameters.AddWithValue("embedding", FormatVector(vector));
        await cmd.ExecuteNonQueryAsync(ct);
    }

    public async Task<DocumentRecord?> GetAsync(string id, CancellationToken ct = default)
    {
        await using var conn = new Npgsql.NpgsqlConnection(_connectionString);
        await conn.OpenAsync(ct);

        await using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            SELECT id, content, array_length(embedding::real[], 1), created_at
            FROM documents WHERE id = @id
            """;
        cmd.Parameters.AddWithValue("id", id);

        await using var reader = await cmd.ExecuteReaderAsync(ct);
        if (!await reader.ReadAsync(ct))
            return null;

        return new DocumentRecord(
            reader.GetString(0),
            reader.GetString(1),
            reader.GetInt32(2),
            reader.GetDateTime(3));
    }

    private static string FormatVector(float[] vector) =>
        "[" + string.Join(",", vector.Select(v => v.ToString("0.####", System.Globalization.CultureInfo.InvariantCulture))) + "]";
}