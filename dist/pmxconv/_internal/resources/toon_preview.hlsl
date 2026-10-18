#include "toon_ramp.hlsl"
Texture2D PreviewAlbedo : register(t0);
SamplerState PreviewSampler : register(s0);
struct VertexInput
{
    float3 position : POSITION;
    float3 normal : NORMAL;
    float2 uv : TEXCOORD0;
    float3 view : TEXCOORD1;
};
struct PixelInput
{
    float4 position : SV_Position;
    float3 normal : TEXCOORD0;
    float2 uv : TEXCOORD1;
    float3 view : TEXCOORD2;
};
PixelInput preview_vs(VertexInput i)
{
    PixelInput o;
    o.position=float4(i.position,1);
    o.normal=i.normal;
    o.uv=i.uv;
    o.view=i.view;
    return o;
}
float4 preview_ps(PixelInput v) : SV_Target
{
    Input i;
    i.albedo=PreviewAlbedo.Sample(PreviewSampler,v.uv).rgb;
    i.normal=normalize(v.normal);
    i.view=v.view;
    float diffuse=saturate(dot(i.normal,normalize(sun.xyz)));
    i.direct=i.albedo*(0.20+0.85*diffuse)*sun.w;
    i.indirect=i.albedo*0.065*sun.w;
    return main(i).direct;
}
