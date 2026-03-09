export const BOARD_TILES = [
  { id: 0, type: 'start', name: '启程门' },
  { id: 1, type: 'city', name: '长安', price: 120, toll: 30 },
  { id: 2, type: 'event', name: '机缘签' },
  { id: 3, type: 'city', name: '洛阳', price: 140, toll: 35 },
  { id: 4, type: 'tax', name: '漕运税', amount: 60 },
  { id: 5, type: 'utility', name: '京杭大运河电站', price: 180, tollMultiplier: 8 },
  { id: 6, type: 'city', name: '扬州', price: 160, toll: 40 },
  { id: 7, type: 'airport', name: '长安机场', price: 200, toll: 50 },
  { id: 8, type: 'event', name: '朝廷令' },
  { id: 9, type: 'city', name: '苏州', price: 180, toll: 45 },
  { id: 10, type: 'jail', name: '大理寺' },
  { id: 11, type: 'city', name: '杭州', price: 200, toll: 55 },
  { id: 12, type: 'event', name: '机缘签' },
  { id: 13, type: 'city', name: '成都', price: 220, toll: 60 },
  { id: 14, type: 'tax', name: '盐铁税', amount: 80 },
  { id: 15, type: 'airport', name: '临安机场', price: 220, toll: 55 },
  { id: 16, type: 'city', name: '广州', price: 230, toll: 65 },
  { id: 17, type: 'event', name: '朝廷令' },
  { id: 18, type: 'city', name: '泉州', price: 240, toll: 70 },
  { id: 19, type: 'utility', name: '都江堰水电站', price: 200, tollMultiplier: 10 },
  { id: 20, type: 'free', name: '茶馆歇脚' },
  { id: 21, type: 'city', name: '临安', price: 250, toll: 75 },
  { id: 22, type: 'event', name: '机缘签' },
  { id: 23, type: 'landmark', name: '紫禁城', price: 300, toll: 90 }
];

export const PURCHASABLE_TYPES = new Set(['city', 'utility', 'airport', 'landmark']);
