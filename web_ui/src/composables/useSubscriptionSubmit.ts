import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { Message } from '@arco-design/web-vue'

export function useSubscriptionSubmit() {
  const router = useRouter()
  const loading = ref(false)

  async function submit(
    formRef: any,
    submitFn: () => Promise<any>,
    successMsg: string = '订阅添加成功',
  ) {
    if (loading.value) return

    try {
      await formRef.value?.validate()
    } catch (error: any) {
      Message.error(error?.errors?.join('\n') || '表单验证失败，请检查输入内容')
      return
    }

    loading.value = true
    try {
      await submitFn()
      Message.success(successMsg)
      router.push('/')
    } catch (error: any) {
      Message.error(error?.message || '操作失败')
    } finally {
      loading.value = false
    }
  }

  return { loading, submit }
}
