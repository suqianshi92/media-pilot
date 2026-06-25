import { RouterProvider } from 'react-router-dom'

import { createAppRouter } from '@/app/router'

const appRouter = createAppRouter()

function App() {
  return <RouterProvider router={appRouter} />
}

export default App
