<template>
  <div class="add-subscription">
    <a-page-header
      title="添加订阅"
      subtitle="添加新的订阅源"
      :show-back="true"
      @back="goBack"
    />

    <a-card>
      <a-tabs v-model:active-key="sourceType" type="rounded">
        <a-tab-pane key="wechat" title="微信公众号">
          <a-space direction="vertical" size="large">
            <a-space>
              <a-link @click="openDialog()">通过公众号码文章获取</a-link>
            </a-space>
          </a-space>

          <div v-if="modalVisible">
            <a-input
              v-model="articleLink"
              placeholder="请输入一个公众号文章链接地址"
              style="width: 300px; margin-bottom: 10px"
            />
            <a-button @click="handleGetMpInfo" :loading="isFetching"
              >获取</a-button
            >
          </div>

          <a-form
            ref="formRef"
            :model="form"
            :rules="rules"
            layout="vertical"
            @submit="handleSubmit"
          >
            <a-form-item label="公众号名称" field="name">
              <a-space>
                <a-select
                  v-model="form.name"
                  placeholder="请输入公众号名称"
                  allow-clear
                  allow-search
                  @search="handleSearch"
                >
                  <a-option
                    v-for="item of searchResults"
                    :value="item.nickname"
                    :label="item.nickname"
                    @click="handleSelect(item)"
                  />
                </a-select>
              </a-space>
            </a-form-item>

            <a-form-item label="头像" field="avatar">
              <a-avatar
                :src="avatar_url"
                v-model="form.avatar"
                placeholder="头像"
              >
                <img :src="avatar_url" width="80" />
              </a-avatar>
            </a-form-item>
            <a-form-item label="公众号ID" field="accountId">
              <a-input
                readonly="readonly"
                v-model="form.wx_id"
                placeholder="请输入公众号ID"
              >
                <template #prefix><icon-idcard /></template>
              </a-input>
            </a-form-item>

            <a-form-item label="描述" field="description">
              <a-textarea
                v-model="form.description"
                placeholder="请输入公众号描述"
                :auto-size="{ minRows: 3, maxRows: 5 }"
                allow-clear
              />
            </a-form-item>

            <a-form-item>
              <a-space>
                <a-button
                  type="primary"
                  html-type="submit"
                  :loading="wxLoading"
                >
                  添加订阅
                </a-button>
                <a-button @click="resetForm">重置</a-button>
              </a-space>
            </a-form-item>
          </a-form>
        </a-tab-pane>

        <a-tab-pane key="xueqiu" title="雪球用户">
          <a-form
            ref="xqFormRef"
            :model="xqForm"
            :rules="xqRules"
            layout="vertical"
            @submit="handleXueqiuSubmit"
          >
            <a-form-item label="搜索雪球用户" field="user_id">
              <a-select
                v-model="xqForm.user_id"
                placeholder="输入用户名搜索"
                allow-clear
                allow-search
                :filter-option="false"
                @search="handleXueqiuSearch"
              >
                <a-option
                  v-for="u of xqSearchResults"
                  :key="u.user_id"
                  :value="String(u.user_id)"
                  :label="u.screen_name"
                  @click="handleXueqiuSelect(u)"
                >
                  <div style="display: flex; align-items: center; gap: 8px">
                    <a-avatar
                      :size="24"
                      :src="u.profile_image_url || avatar_url"
                      placeholder="头像"
                    />
                    <span>{{ u.screen_name }}</span>
                    <span style="color: var(--color-text-3); font-size: 12px">
                      粉丝 {{ u.followers_count }}
                    </span>
                    <a-tag v-if="u.verified" size="small" color="blue"
                      >认证</a-tag
                    >
                  </div>
                </a-option>
              </a-select>
            </a-form-item>

            <a-form-item label="头像" field="profile_image_url">
              <a-avatar
                :size="48"
                :src="xqForm.profile_image_url || avatar_url"
                placeholder="头像"
              />
            </a-form-item>

            <a-form-item label="用户ID" field="user_id">
              <a-input
                readonly
                v-model="xqForm.user_id"
                placeholder="选择用户后自动填充"
              >
                <template #prefix><icon-idcard /></template>
              </a-input>
            </a-form-item>

            <a-form-item label="描述" field="description">
              <a-textarea
                v-model="xqForm.description"
                placeholder="请输入用户描述"
                :auto-size="{ minRows: 3, maxRows: 5 }"
                allow-clear
              />
            </a-form-item>

            <a-form-item>
              <a-space>
                <a-button
                  type="primary"
                  html-type="submit"
                  :loading="xqLoading"
                >
                  添加雪球订阅
                </a-button>
                <a-button @click="resetXqForm">重置</a-button>
              </a-space>
            </a-form-item>
          </a-form>
        </a-tab-pane>
      </a-tabs>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from "vue";
import { useRouter } from "vue-router";
import { Message } from "@arco-design/web-vue";
import {
  addSubscription,
  searchBiz,
  getSubscriptionInfo,
} from "@/api/subscription";
import {
  searchXueqiuUser,
  addXueqiuSubscription,
  type XueqiuUser,
} from "@/api/xueqiu";
import { Avatar } from "@/utils/constants";
import { useSubscriptionSubmit } from "@/composables/useSubscriptionSubmit";

const router = useRouter();
const { submit: wxSubmit, loading: wxLoading } = useSubscriptionSubmit();
const { submit: xqSubmit, loading: xqLoading } = useSubscriptionSubmit();

const isFetching = ref(false);
const searchResults = ref([]);
const avatar_url = ref("/static/default-avatar.png");
const formRef = ref(null);
const sourceType = ref("wechat");
const form = ref({
  name: "",
  wx_id: "",
  avatar: "",
  description: "",
});

const xqFormRef = ref(null);
const xqSearchResults = ref<XueqiuUser[]>([]);
const xqForm = ref({
  user_id: "",
  screen_name: "",
  profile_image_url: "",
  description: "",
});

const xqRules = {
  user_id: [{ required: true, message: "请搜索并选择雪球用户" }],
  profile_image_url: [{ required: true, message: "请选择用户以获取头像" }],
  description: [{ max: 200, message: "描述不能超过200个字符" }],
};

watch(
  () => form.value.avatar,
  (newValue) => {
    avatar_url.value = Avatar(newValue);
  },
  { deep: true },
);

const rules = {
  name: [
    { required: true, message: "请输入公众号名称" },
    { min: 2, max: 30, message: "公众号名称长度应在2-30个字符之间" },
  ],
  wx_id: [
    { required: true, message: "请输入公众号ID" },
    {
      pattern: /^[a-zA-Z0-9_-]+$/,
      message: "公众号ID只能包含字母、数字、下划线和横线",
    },
  ],
  avatar: [
    {
      required: true,
      message: "请选择公众号头像",
      validator: (value: string) => {
        return value && value.startsWith("http");
      },
      message: "请选择有效的头像URL",
    },
  ],
  description: [{ max: 200, message: "描述不能超过200个字符" }],
};

const handleSearch = async (value: string) => {
  if (!value) {
    searchResults.value = [];
    return;
  }
  try {
    const res = await searchBiz(value, {
      kw: value,
      offset: 0,
      limit: 10,
    });
    searchResults.value = res.list || [];
  } catch (error) {
    searchResults.value = [];
  }
};

const handleGetMpInfo = async () => {
  if (isFetching.value) return false;
  if (!articleLink.value) {
    Message.error("请提供一个公众号文章链接");
    return false;
  }
  isFetching.value = true;
  try {
    const res = await getSubscriptionInfo(articleLink.value.trim());
    const info = res?.mp_info || false;
    if (info) {
      form.value.name = info.mp_name || "";
      form.value.description = info.mp_name || "";
      form.value.wx_id = info.biz || "";
      form.value.avatar = info.logo || "";
    }
  } catch (error) {
    Message.error("获取公众号信息失败");
    return false;
  } finally {
    isFetching.value = false;
  }
  modalVisible.value = false;
  return true;
};

const handleSelect = (item: any) => {
  form.value.name = item.nickname;
  form.value.wx_id = item.fakeid;
  form.value.description = item.signature;
  form.value.avatar = item.round_head_img;
};

const handleSubmit = async () => {
  await wxSubmit(
    formRef,
    async () => {
      await addSubscription({
        mp_name: form.value.name,
        mp_id: form.value.wx_id,
        avatar: form.value.avatar,
        mp_intro: form.value.description,
      });
    },
    "订阅添加成功",
  );
};

const resetForm = () => {
  form.value = {
    name: "",
    wx_id: "",
    avatar: "",
    description: "",
  };
  searchResults.value = [];
};

const modalVisible = ref(false);
const articleLink = ref("");

const openDialog = () => {
  modalVisible.value = true;
};

const handleXueqiuSearch = async (value: string) => {
  if (!value || value.length < 1) {
    xqSearchResults.value = [];
    return;
  }
  try {
    const res = await searchXueqiuUser(value);
    xqSearchResults.value = res?.list || [];
  } catch (error) {
    xqSearchResults.value = [];
  }
};

const handleXueqiuSelect = (user: XueqiuUser) => {
  xqForm.value.user_id = String(user.user_id);
  xqForm.value.screen_name = user.screen_name;
  xqForm.value.profile_image_url = user.profile_image_url || "";
  xqForm.value.description = user.description || "";
};

const handleXueqiuSubmit = async () => {
  await xqSubmit(
    xqFormRef,
    async () => {
      const { user_id, screen_name, profile_image_url, description } =
        xqForm.value;
      await addXueqiuSubscription({
        user_id,
        screen_name,
        avatar: profile_image_url,
        description,
      });
    },
    "雪球订阅添加成功",
  );
};

const resetXqForm = () => {
  xqForm.value = {
    user_id: "",
    screen_name: "",
    profile_image_url: "",
    description: "",
  };
  xqSearchResults.value = [];
};

const goBack = () => {
  router.go(-1);
};
</script>

<style scoped>
.add-subscription {
  padding: 20px;
  max-width: 800px;
  margin: 0 auto;
}

.arco-form-item {
  margin-bottom: 20px;
}
</style>
