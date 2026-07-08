// 高德北京底图 + 地铁线路叠加
const darkStyle = {
  version: 8 as const,
  name: 'BJTUMetroSim',
  glyphs: 'https://fonts.openmaptiles.org/{fontstack}/{range}.pbf',
  sources: {
    amap: {
      type: 'raster' as const,
      tiles: [
        'https://webrd01.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
      ],
      tileSize: 256,
      minzoom: 0,
      maxzoom: 18,
      attribution: '&copy; 高德地图',
    },
  },
  layers: [
    {
      id: 'amap-layer',
      type: 'raster',
      source: 'amap',
      paint: {
        'raster-opacity': 0.55,
        'raster-saturation': -0.5,
        'raster-brightness-min': 0.1,
        'raster-brightness-max': 0.5,
      },
    },
  ],
};

export default darkStyle;
