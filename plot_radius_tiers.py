"""Generate map of Kelheim scenario radius tiers using plotly."""
import geopandas as gpd
import plotly.graph_objects as go
from shapely.geometry import Point
import numpy as np
import json

print('Loading data...')
gpkg = 'data/germany/tmp_vg250/vg250-ew_12-31.utm32s.gpkg.ebenen/vg250-ew_ebenen_1231/DE_VG250.gpkg'
krs = gpd.read_file(gpkg, layer='vg250_krs')
bav = krs[krs['ARS'].str.startswith('09')].copy()

kelheim_center = Point(709000, 5423000)
bav['border_dist_km'] = bav.geometry.distance(kelheim_center) / 1000.0
bav['ars5'] = bav['ARS'].str[:5]

# Drop timestamp columns that break JSON serialization
for col in bav.columns:
    if hasattr(bav[col], 'dt') or bav[col].dtype == 'datetime64[ns]':
        bav = bav.drop(columns=[col])
# Drop BEGINN if present (Timestamp)
if 'BEGINN' in bav.columns:
    bav = bav.drop(columns=['BEGINN'])
if 'WSK' in bav.columns:
    bav = bav.drop(columns=['WSK'])

# Convert to WGS84 for plotly
bav_wgs = bav.to_crs(epsg=4326)

current_7 = {'09273', '09375', '09186', '09274', '09176', '09178', '09373'}
tier_50 = set(bav[bav['border_dist_km'] <= 50]['ars5'])
tier_70 = set(bav[bav['border_dist_km'] <= 70]['ars5'])


def classify(row):
    ars = row['ars5']
    if ars == '09273':
        return 'Kelheim (core) - 127k'
    elif ars in current_7:
        return 'Current 7 Kreise - 1.07M'
    elif ars in tier_50:
        return '50km tier - 2.44M'
    elif ars in tier_70:
        return '70km tier - 4.00M'
    else:
        return 'Rest of Bavaria'


bav_wgs['tier'] = bav_wgs.apply(classify, axis=1)
bav_wgs['pop_fmt'] = bav_wgs['EWZ'].apply(lambda x: f'{x:,}')
bav_wgs['label'] = bav_wgs.apply(
    lambda r: f"{r['GEN']} ({r['BEZ']})<br>Pop: {r['pop_fmt']}<br>Border dist: {r['border_dist_km']:.0f}km",
    axis=1
)

color_map = {
    'Kelheim (core) - 127k': '#c0392b',
    'Current 7 Kreise - 1.07M': '#e74c3c',
    '50km tier - 2.44M': '#f39c12',
    '70km tier - 4.00M': '#3498db',
    'Rest of Bavaria': '#dfe6e9',
}

tier_order = [
    'Kelheim (core) - 127k',
    'Current 7 Kreise - 1.07M',
    '50km tier - 2.44M',
    '70km tier - 4.00M',
    'Rest of Bavaria',
]

print('Building map...')
fig = go.Figure()

# Plot each tier as separate trace for legend
for tier_name in tier_order:
    subset = bav_wgs[bav_wgs['tier'] == tier_name]
    if len(subset) == 0:
        continue

    geojson = json.loads(subset.to_json())

    fig.add_trace(go.Choropleth(
        geojson=geojson,
        locations=subset.index,
        z=[tier_order.index(tier_name)] * len(subset),
        colorscale=[[0, color_map[tier_name]], [1, color_map[tier_name]]],
        showscale=False,
        name=tier_name,
        showlegend=True,
        text=subset['label'],
        hoverinfo='text',
        marker=dict(line=dict(width=0.5, color='#636e72')),
    ))

# Kelheim center point (~48.917N, 11.867E)
kelheim_lat, kelheim_lon = 48.917, 11.867

fig.add_trace(go.Scattergeo(
    lat=[kelheim_lat], lon=[kelheim_lon],
    mode='markers+text',
    marker=dict(size=14, color='white', symbol='star',
                line=dict(width=2, color='black')),
    text=['Kelheim'],
    textposition='top center',
    textfont=dict(size=11, color='black', family='Arial Black'),
    name='Kelheim center',
    showlegend=True,
))

# Draw radius circles (approximate, in degrees)
# At 48.9N: 1 deg lat ~ 111km, 1 deg lon ~ 73km
for radius_km, dash, color, label in [
    (50, 'solid', '#2c3e50', '50 km radius'),
    (70, 'solid', '#2c3e50', '70 km radius'),
    (100, 'dash', '#7f8c8d', '100 km radius'),
]:
    angles = np.linspace(0, 2 * np.pi, 100)
    lats = kelheim_lat + (radius_km / 111.0) * np.sin(angles)
    lons = kelheim_lon + (radius_km / 73.0) * np.cos(angles)
    fig.add_trace(go.Scattergeo(
        lat=lats, lon=lons,
        mode='lines',
        line=dict(width=2.5, color=color, dash=dash),
        name=label,
        showlegend=True,
    ))

# City labels
cities = [
    ('Regensburg', 49.015, 12.10, True),
    ('Ingolstadt', 48.766, 11.425, False),
    ('Munchen', 48.137, 11.575, True),
    ('Nurnberg', 49.452, 11.077, True),
    ('Landshut', 48.537, 12.152, False),
    ('Straubing', 48.882, 12.573, False),
    ('Augsburg', 48.366, 10.898, False),
    ('Schwandorf', 49.326, 12.110, False),
    ('Cham', 49.222, 12.662, False),
    ('Deggendorf', 48.832, 12.964, False),
    ('Passau', 48.574, 13.465, False),
    ('Freising', 48.402, 11.749, False),
    ('Neumarkt', 49.280, 11.462, False),
    ('Amberg', 49.445, 11.860, False),
]

for city, lat, lon, bold in cities:
    fig.add_trace(go.Scattergeo(
        lat=[lat], lon=[lon],
        mode='text',
        text=[f'<b>{city}</b>' if bold else city],
        textfont=dict(size=10 if bold else 8, color='#2d3436'),
        showlegend=False,
    ))

fig.update_geos(
    visible=False,
    resolution=50,
    showcountries=True,
    countrycolor='#b2bec3',
    showland=True,
    landcolor='#f5f6fa',
    fitbounds='locations',
    projection_type='mercator',
)

fig.update_layout(
    title=dict(
        text='Kelheim Extended Scenario — Landkreis Coverage by Radius<br>'
             '<sub>Border distance from Kelheim center | Population from Zensus 2022</sub>',
        font=dict(size=18),
        x=0.5,
    ),
    legend=dict(
        x=0.01, y=0.99,
        bgcolor='rgba(255,255,255,0.9)',
        bordercolor='#636e72',
        borderwidth=1,
        font=dict(size=12),
    ),
    width=1000,
    height=900,
    margin=dict(l=10, r=10, t=80, b=10),
)

outpath = 'C:/Users/VWAUCCY/dev/msf/projects/Dissertation/matsim_scenarios/bavaria/output/kelheim_radius_tiers.html'
fig.write_html(outpath, include_plotlyjs='cdn')
print(f'Saved interactive map to {outpath}')

# Also try static image
try:
    imgpath = outpath.replace('.html', '.png')
    fig.write_image(imgpath, width=1200, height=1000, scale=2)
    print(f'Saved static image to {imgpath}')
except Exception as e:
    print(f'Static image export not available ({e}), use HTML version')
