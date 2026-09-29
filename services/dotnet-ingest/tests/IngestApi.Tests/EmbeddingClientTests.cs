using AiPlatform.IngestApi.Services;
using Microsoft.Extensions.Configuration;
using Xunit;

namespace AiPlatform.IngestApi.Tests;

public class EmbeddingClientTests
{
    private static EmbeddingClient CreateClient(string mode = "mock") =>
        new(
            new ConfigurationBuilder().AddInMemoryCollection(
                new Dictionary<string, string?>
                {
                    ["Embedding:Mode"] = mode,
                    ["Embedding:Dimensions"] = "384"
                }).Build(),
            httpFactory: null);

    [Fact]
    public async Task MockEmbedding_IsDeterministic_AndNormalized()
    {
        var client = CreateClient();

        var a = await client.EmbedAsync("refund policy allows refunds within 30 days");
        var b = await client.EmbedAsync("refund policy allows refunds within 30 days");
        var c = await client.EmbedAsync("a completely different topic");

        Assert.Equal(a, b);
        Assert.NotEqual(a, c);
        Assert.Equal(384, a.Length);
        var norm = Math.Sqrt(a.Sum(v => (double)v * v));
        Assert.Equal(1.0, norm, 5);
    }

    [Fact]
    public async Task MockEmbedding_DifferentText_Differs()
    {
        var client = CreateClient();
        var a = await client.EmbedAsync("returns are allowed");
        var b = await client.EmbedAsync("hosting on kubernetes");
        Assert.NotEqual(a, b);
    }
}