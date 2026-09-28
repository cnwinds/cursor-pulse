<template>
  <div class="pulse-auth-shell">
    <el-card shadow="never" class="pulse-auth-card">
      <el-result icon="warning" title="无访问权限" sub-title="请联系管理员为你开通后台角色。">
        <template #extra>
          <el-button type="primary" @click="$router.push('/')">返回首页</el-button>
        </template>
      </el-result>
    </el-card>
  </div>
</template>
