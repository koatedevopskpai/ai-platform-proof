namespace AiPlatform.IngestApi.Services;

public interface IEmbeddingClient
{
    Task<float[]> EmbedAsync(string text, CancellationToken ct = default);
}

public sealed class EmbeddingClient(IConfiguration config, IHttpClientFactory? httpFactory)
    : IEmbeddingClient
{
    private readonly string _mode = config["Embedding:Mode"] ?? "mock";
    private readonly int _dimensions = config.GetValue("Embedding:Dimensions", 384);

    public async Task<float[]> EmbedAsync(string text, CancellationToken ct = default)
    {
        if (_mode.Equals("openai", StringComparison.OrdinalIgnoreCase))
        {
            var openAiKey = config["OpenAI:ApiKey"];
            if (!string.IsNullOrWhiteSpace(openAiKey))
                return await EmbedOpenAiAsync(text, openAiKey, ct);
        }

        return EmbedMock(text);
    }

    private async Task<float[]> EmbedOpenAiAsync(string text, string apiKey, CancellationToken ct)
    {
        var baseUrl = config["OpenAI:BaseUrl"] ?? "https://api.openai.com/v1";
        var model = config["OpenAI:EmbeddingModel"] ?? "text-embedding-3-small";

        using var client = httpFactory!.CreateClient();
        client.DefaultRequestHeaders.Authorization =
            new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", apiKey);

        var payload = new
        {
            model,
            input = new[] { text }
        };

        var resp = await client.PostAsJsonAsync($"{baseUrl}/embeddings", payload, ct);
        resp.EnsureSuccessStatusCode();

        var body = await resp.Content.ReadFromJsonAsync<OpenAiEmbeddingResponse>(ct);
        return body?.Data?[0]?.Embedding ?? throw new InvalidOperationException("empty embedding response");
    }

    private float[] EmbedMock(string text)
    {
        var vec = new float[_dimensions];

        // Character trigram hashing, byte-identical to the Python mock embedder so
        // the shared pgvector store is interoperable across languages.
        foreach (var word in text.ToLowerInvariant().Split([' ', '\t', '\n'], StringSplitOptions.RemoveEmptyEntries))
        {
            var padded = $"#{word}#";
            for (var i = 0; i <= padded.Length - 3; i++)
            {
                var tri = padded.Substring(i, 3);
                var hash = System.Security.Cryptography.SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(tri));
                var idx = BitConverter.ToUInt32(hash, 0) % (uint)_dimensions;
                var sign = (hash[4] % 2 == 0) ? 1.0f : -1.0f;
                vec[idx] += sign;
            }
        }

        var norm = (float)Math.Sqrt(vec.Sum(v => (double)v * v));
        if (norm < float.Epsilon)
            return vec;

        for (var i = 0; i < vec.Length; i++)
            vec[i] /= norm;

        return vec;
    }

    private sealed record OpenAiEmbeddingResponse(List<OpenAiEmbeddingDatum>? Data);
    private sealed record OpenAiEmbeddingDatum(float[] Embedding);
}