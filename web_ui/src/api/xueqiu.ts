import http from './http'

export interface XueqiuUser {
  user_id: number | string
  screen_name: string
  description: string
  followers_count: number
  friends_count: number
  status_count: number
  gender: string
  province: string
  city: string
  verified: boolean
  verified_type: number
  profile: string
  profile_image_url: string
  domain: string
}

export interface XueqiuSearchResult {
  code: number
  data: {
    list: XueqiuUser[]
    total: number
    page: number
    max_page: number
  }
}

export interface XueqiuSubscription {
  id: string
  mp_name: string
  mp_cover: string
  mp_intro: string
  status: number
  source_type: string
  extinfo: Record<string, any>
  sync_time: number
  update_time: number
  created_at: string
}

export interface XueqiuSubscriptionListResult {
  code: number
  data: {
    list: XueqiuSubscription[]
    total: number
    page: { limit: number; offset: number; total: number }
  }
}


export const searchXueqiuUser = (q: string, page = 1, size = 5) => {
  return http.get<XueqiuSearchResult>('/wx/xueqiu/search/user', {
    params: { q, page, size },
  })
}

export const getXueqiuTimeline = (userId: string, page = 1, type = '0') => {
  return http.get('/wx/xueqiu/statuses/user_timeline', {
    params: { user_id: userId, page, type },
  })
}

export const getXueqiuSubscriptions = (limit = 10, offset = 0) => {
  return http.get<XueqiuSubscriptionListResult>('/wx/xueqiu', {
    params: { limit, offset },
  })
}

export const addXueqiuSubscription = (data: {
  user_id: string
  screen_name: string
  avatar?: string
  description?: string
}) => {
  return http.post('/wx/xueqiu', data)
}

export const deleteXueqiuSubscription = (feedId: string) => {
  return http.delete(`/wx/xueqiu/${feedId}`)
}

export const updateXueqiuArticles = (feedId: string) => {
  return http.get(`/wx/xueqiu/update/${feedId}`)
}

export const getXueqiuHealth = () => {
  return http.get('/wx/xueqiu/health')
}
