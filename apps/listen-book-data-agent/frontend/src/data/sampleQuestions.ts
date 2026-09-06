export interface SampleCategory {
  category: string;
  questions: string[];
}

/** Example prompts grouped by business domain (from the PRD scenarios). */
export const SAMPLE_QUESTIONS: SampleCategory[] = [
  {
    category: '内容',
    questions: ['平台一共有多少个有声专辑', '哪个分类下的专辑数量最多', '本月新增了多少个音频章节'],
  },
  {
    category: '播放',
    questions: [
      '最近7天播放量最高的前10个专辑',
      '本月播放完成率按天趋势',
      '最近30天的播放次数是多少',
    ],
  },
  {
    category: '用户',
    questions: [
      '用户收藏最多的前10个专辑',
      '评论评分最高的内容是什么',
      '最近7天新增用户数量按天趋势',
    ],
  },
  {
    category: '交易',
    questions: ['本月内容订单金额是多少', '最近7天退款金额是多少', 'VIP月度会员套餐销量如何'],
  },
  {
    category: '搜索推荐',
    questions: ['热播榜周榜前十是什么', '热搜榜月榜有哪些搜索词', '首页每日推荐位展示了哪些专辑'],
  },
];
