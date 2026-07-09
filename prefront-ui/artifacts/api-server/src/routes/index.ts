import { Router, type IRouter } from "express";
import healthRouter from "./health";
import auditRouter from "./audit";
import decisionsRouter from "./decisions";

const router: IRouter = Router();

router.use(healthRouter);
router.use(auditRouter);
router.use(decisionsRouter);

export default router;
