// Injected before the native fog stage. Inputs retain scene lighting and shadows.
cbuffer ToonMaterial : register(b0)
{
    float4 ramp;       // kind, shadow boundary, band width, strength
    float4 shadow;     // RGB shadow tint, highlight strength
};
cbuffer ToonLight : register(b1) { float4 sun; };
struct Input
{
    float3 direct : TEXCOORD0;
    float3 indirect : TEXCOORD1;
    float3 albedo : TEXCOORD2;
    float3 normal : TEXCOORD3;
    float3 view : TEXCOORD4;
};
struct Output { float4 direct : SV_Target0; float4 indirect : SV_Target1; };
Output main(Input i)
{
    float3 base=max(i.albedo,0.002);
    float3 scene=max(i.direct+i.indirect,0);
    float energy=dot(scene,float3(0.2126,0.7152,0.0722))/max(dot(base,float3(0.2126,0.7152,0.0722)),0.01);
    float brightness=min(energy,1.15);
    float band=smoothstep(ramp.y-ramp.z,ramp.y+ramp.z,brightness);
    float warmSkin=saturate((base.r-base.g-0.025)*18)*saturate((base.r-base.b-0.04)*16);
    float mixedBody=(ramp.x>2.5 && ramp.x<3.5)?1:0;
    float3 shadowTint=lerp(shadow.rgb,float3(0.86,0.76,0.81),warmSkin*mixedBody);
    float3 shadowColor=base*shadowTint;
    float3 lightColor=base*0.94;
    float3 toon=lerp(shadowColor,lightColor,band);
    // Keep dark rooms dark; the ramp does not add an emissive term.
    toon*=saturate(energy/0.35);
    float3 illumination=scene/max(dot(scene,float3(0.2126,0.7152,0.0722)),0.01);
    toon*=lerp(1.0,clamp(illumination,0.75,1.25),0.16);
    float3 normal=normalize(i.normal);
    float3 halfVector=normalize(normalize(sun.xyz)-normalize(i.view));
    float spec=smoothstep(0.86,0.93,saturate(dot(normal,halfVector)));
    float highlight=lerp(shadow.w,0.012,warmSkin*mixedBody);
    toon+=base*spec*highlight*saturate(energy);
    Output o;
    o.direct=float4(lerp(scene,toon,saturate(ramp.w)),1);
    o.indirect=0;
    return o;
}
