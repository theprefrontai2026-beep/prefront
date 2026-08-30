import { Router, type IRouter } from "express";
import healthRouter from "./health";
import auditRouter from "./audit";
import decisionsRouter from "./decisions";
import settingsRouter from "./settings";

const router: IRouter = Router();

router.use(healthRouter);
router.use(auditRouter);
router.use(decisionsRouter);
router.use(settingsRouter);

export default router;
